#!/usr/bin/env python
# vim: expandtab ts=4 sw=4 sts=4 fileencoding=utf-8:
#
# Copyright (c) 2007-2014 Thomas Pani, Jeremy Grossmann & Alexey Eromenko "Technologov"
#
# Contributions by Pavel Skovajsa
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#

"""
# This module is used for actual control of VMs, sending commands to VBox controllers.
# VBox controllers implement VirtualBox version-specific API calls.
# This is the server part, it can be started manually, or automatically from GNS3.
"""

from __future__ import print_function

import csv
import cStringIO
import os
import select
import socket
import sys
import threading
import SocketServer
import time
import re
import tempfile

from tcp_pipe_proxy import PipeProxy
from vboxcontroller_4_3 import VBoxController_4_3

import logging
logging.basicConfig()
log = logging.getLogger(__name__)
log.setLevel("INFO")


if sys.platform.startswith("win"):
    # automatically generate the Typelib wrapper
    import win32com.client
    win32com.client.gencache.is_readonly = False
    win32com.client.gencache.GetGeneratePath()
    import win32file
    import msvcrt

try:
    reload(sys)
    sys.setdefaultencoding('utf-8')
except:
    sys.stderr.write("Can't set default encoding to utf-8\n")

__author__ = 'Thomas Pani, Jeremy Grossmann and Alexey Eromenko "Technologov"'
__version__ = '0.9'

PORT = 11525
IP = ""
VBOX_INSTANCES = {}
FORCE_IPV6 = False
VBOX_STREAM = 0
VBOXVER = 0.0
VBOXVER_REQUIRED = 4.1
CACHED_REPLY = ""
CACHED_REQUEST = ""
CACHED_TIME = 0.0
g_stats=""
g_vboxManager = 0
g_result=""

try:
    from vboxapi import VirtualBoxManager
    g_vboxManager = VirtualBoxManager(None, None)
except:
    pass

#Working Dir in VirtualBox is mainly needed for "Traffic Captures".
WORKDIR = os.getcwdu()
if os.environ.has_key("TEMP"):
    WORKDIR = os.environ["TEMP"]
elif os.environ.has_key("TMP"):
    WORKDIR = os.environ["TMP"]

class UDPConnection:

    def __init__(self, sport, daddr, dport):
        self.lport = sport
        self.rhost = daddr
        self.rport = dport

    def resolve_names(self):
        try:
            addr = socket.gethostbyname(self.rhost)
            self.rhost = addr
        except socket.error as e:
            log.error("Unable to resolve hostname {}: {}".format(self.rhost, e))

class xVBOXInstance(object):

    def __init__(self, name):

        self.name = name
        self.console = ''
        self.image = ''
        self.nic = {}
        self.nics = '6'
        self.udp = {}
        self.capture = {}
        self.netcard = 'Automatic'
        self.guestcontrol_user = ''
        self.guestcontrol_password = ''
        self.headless_mode = False
        self.console_support = False
        self.console_telnet_server = False
        self.process = None
        self.pipeThread = None
        self.pipe = None
        self.workdir = WORKDIR + os.sep + name
        self.valid_attr_names = ['image',
                                 'console',
                                 'nics',
                                 'netcard',
                                 'guestcontrol_user',
                                 'guestcontrol_password',
                                 'headless_mode',
                                 'console_support',
                                 'console_telnet_server']
        self.mgr = g_vboxManager
        self.vbox = self.mgr.vbox

        self.vbc = VBoxController_4_3(self.mgr)
        # Init win32 com
        if sys.platform == 'win32':
            self.prepareWindowsCOM()

    def prepareWindowsCOM(self):
        # Microsoft COM behaves differently than Mozilla XPCOM, and requires special multi-threading code.
        # Get the VBox interface from previous thread
        global VBOX_STREAM
        i = pythoncom.CoGetInterfaceAndReleaseStream(VBOX_STREAM, pythoncom.IID_IDispatch)
        self.vbox = win32com.client.Dispatch(i)
        VBOX_STREAM = pythoncom.CoMarshalInterThreadInterfaceInStream(pythoncom.IID_IDispatch, self.vbox)

    def create(self):
        pass

    def clean(self):
        pass

    def start(self):

        log.debug("start")
        global WORKDIR
        self.vmname = self.image

        if self.console_support:
            p = re.compile('\s+', re.UNICODE)
            pipe_name = p.sub("_", self.vmname)
            if sys.platform.startswith('win'):
                pipe_name = r"\\.\pipe\VBOX\{}".format(pipe_name)
            else:
                pipe_name = os.path.join(tempfile.gettempdir(), "pipe_{}".format(pipe_name))
        else:
            pipe_name = None

        started = self.vbc.start(self.vmname, self.nics, self.udp, self.capture, self.netcard, self.headless_mode, pipe_name)
        if started:
            self.vbc.setName(self.name)

        if started and self.console_support and int(self.console) and self.console_telnet_server:

            global IP
            if sys.platform.startswith('win'):
                try:
                    self.pipe = open(pipe_name, 'a+b')
                except:
                    return started
                self.pipeThread = PipeProxy(self.vmname, msvcrt.get_osfhandle(self.pipe.fileno()), IP, int(self.console))
                self.pipeThread.setDaemon(True)
                self.pipeThread.start()
            else:
                try:
                    self.pipe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.pipe.connect(pipe_name)
                except socket.error as err:
                    print("connection to pipe %s failed -> %s" % (pipe_name, err[1]))
                    return started

                self.pipeThread = PipeProxy(self.vmname, self.pipe, IP, int(self.console))
                self.pipeThread.setDaemon(True)
                self.pipeThread.start()

        return started

    def reset(self):

        log.debug("reset")
        return self.vbc.reset()

    def status(self):

        log.debug("status")
        return self.vbc.status()

    def stop(self):

        log.debug("stop")
        if self.pipeThread:
            self.pipeThread.stop()
            self.pipeThread.join()
            self.pipeThread = None

        if self.pipe:
            if sys.platform.startswith('win'):
                win32file.CloseHandle(msvcrt.get_osfhandle(self.pipe.fileno()))
            else:
                self.pipe.close()
            self.pipe = None

        return self.vbc.stop()

    def suspend(self):

        log.debug("suspend")
        return self.vbc.suspend()

    def rename(self, new_name):

        log.debug("rename")
        self.name = new_name

    def resume(self):

        log.debug("resume")
        return self.vbc.resume()

    def create_udp(self, i_vnic, sport, daddr, dport):

        log.debug("create_udp")
        return self.vbc.create_udp(int(i_vnic), sport, daddr, dport)

    def delete_udp(self, i_vnic):

        log.debug("delete_udp")
        return self.vbc.delete_udp(int(i_vnic))

class VBOXInstance(xVBOXInstance):

    def __init__(self, name):
        super(VBOXInstance, self).__init__(name)

class VBoxDeviceInstance(VBOXInstance):

    def __init__(self, *args, **kwargs):
        super(VBoxDeviceInstance, self).__init__(*args, **kwargs)
        self.netcard = 'automatic'

class VBoxWrapperRequestHandler(SocketServer.StreamRequestHandler):
    modules = {
        'vboxwrapper' : {
            'version' : (0, 0),
            'parser_test' : (0, 10),
            'module_list' : (0, 0),
            'cmd_list' : (1, 1),
            'working_dir' : (1, 1),
            'reset' : (0, 0),
            'close' : (0, 0),
            'stop' : (0, 0),
            },
        'vbox' : {
            'version' : (0, 0),
            'vm_list' : (0, 0),
            'find_vm' : (1, 1),
            'rename': (2, 2),
            'create' : (2, 2),
            'delete' : (1, 1),
            'setattr' : (3, 3),
            'create_nic' : (2, 2),
            'create_udp' : (5, 5),
            'delete_udp' : (2, 2),
            'create_capture' : (3, 3),
            'delete_capture' : (2, 2),
            'start' : (1, 1),
            'stop' : (1, 1),
            'reset' : (1, 1),
            'status' : (1, 1),
            'suspend' : (1, 1),
            'resume' : (1, 1),
            'clean': (1, 1),
            }
        }

    vbox_classes = {
        'vbox': VBoxDeviceInstance,
        }

    # Dynamips style status codes
    HSC_INFO_OK         = 100  #  ok
    HSC_INFO_MSG        = 101  #  informative message
    HSC_INFO_DEBUG      = 102  #  debugging message
    HSC_ERR_PARSING     = 200  #  parse error
    HSC_ERR_UNK_MODULE  = 201  #  unknown module
    HSC_ERR_UNK_CMD     = 202  #  unknown command
    HSC_ERR_BAD_PARAM   = 203  #  bad number of parameters
    HSC_ERR_INV_PARAM   = 204  #  invalid parameter
    HSC_ERR_BINDING     = 205  #  binding error
    HSC_ERR_CREATE      = 206  #  unable to create object
    HSC_ERR_DELETE      = 207  #  unable to delete object
    HSC_ERR_UNK_OBJ     = 208  #  unknown object
    HSC_ERR_START       = 209  #  unable to start object
    HSC_ERR_STOP        = 210  #  unable to stop object
    HSC_ERR_FILE        = 211  #  file error
    HSC_ERR_BAD_OBJ     = 212  #  bad object

    close_connection = 0

    def send_reply(self, code, done, msg):

        sep = '-'
        if not done:
            sep = ' '
        global CACHED_REPLY, CACHED_TIME
        CACHED_TIME = time.time()
        CACHED_REPLY = "%3d%s%s\r\n" % (code, sep, msg)
        self.wfile.write(CACHED_REPLY)

    def handle(self):

        print("Connection from", self.client_address)
        try:
            self.handle_one_request()
            while not self.close_connection:
                self.handle_one_request()
            print("Disconnection from", self.client_address)
        except socket.error as e:
            log.error("{}".format(e))
            self.request.close()
            return

    def __get_tokens(self, request):

        input_ = cStringIO.StringIO(request)
        tokens = []
        try:
            tokens = csv.reader(input_, delimiter=' ').next()
        except StopIteration:
            pass
        return tokens

    def finish(self):
        pass

    def handle_one_request(self):

        request = self.rfile.readline()

        # Don't process empty strings (this creates Broken Pipe exceptions)
        #FIXME: this causes 100% cpu usage on Windows.
        #if request == "":
        #    return

        # If command exists in cache (=cache hit), we skip further processing
        if self.check_cache(request):
            return
        global CACHED_REQUEST
        CACHED_REQUEST = request
        request = request.rstrip()      # Strip package delimiter.

        # Parse request.
        tokens = self.__get_tokens(request)
        if len(tokens) < 2:
            try:
                self.send_reply(self.HSC_ERR_PARSING, 1, "At least a module and a command must be specified")
            except socket.error:
                self.close_connection = 1
            return

        module, command = tokens[:2]
        data = tokens[2:]

        if not module in self.modules.keys():
            self.send_reply(self.HSC_ERR_UNK_MODULE, 1,
                            "Unknown module '%s'" % module)
            return

        # Prepare to call the do_<command> function.
        mname = 'do_%s_%s' % (module, command)

        if not hasattr(self, mname):
            self.send_reply(self.HSC_ERR_UNK_CMD, 1,
                            "Unknown command '%s'" % command)
            return

        try:
            if len(data) < self.modules[module][command][0] or \
                len(data) > self.modules[module][command][1]:
                self.send_reply(self.HSC_ERR_BAD_PARAM, 1,
                                "Bad number of parameters (%d with min/max=%d/%d)" %
                                    (len(data),
                                      self.modules[module][command][0],
                                      self.modules[module][command][1])
                                    )
                return
        except Exception as e:
            # This can happen, if you add send command, but forget to define it in class modules
            self.send_reply(self.HSC_ERR_INV_PARAM, 1, "Unknown Exception")
            log.error("exception in handle_one_request(): {}".format(e))
            return

        # Call the function.
        method = getattr(self, mname)
        method(data)

    def check_cache(self, request):

        # TCP Server cache is needed due to async nature of the server;
        # Often TCP client (dynagen) received a reply from previous request.
        # This workaround allows us to send two requests and get two replies per query.
        #
        # Checks command cache, and sends cached reply immediately.
        # Returns True, if cached request/reply found within reasonable time period. (=cache hit)
        # Otherwise returns false, which means cache miss, and further processing
        # by handle_one_request() is required.
        global CACHED_REQUEST, CACHED_REPLY, CACHED_TIME
        cur_time = time.time()

        if (cur_time - CACHED_TIME) > 1.0:
            # Too much time elapsed... cache is invalid
            return False
        if request != CACHED_REQUEST:
            # different request means a cache miss
            return False

        CACHED_TIME = 0.0  # Reset timer disallows to use same cache more than 2 times in a row
        self.wfile.write(CACHED_REPLY)
        return True

    def do_vboxwrapper_version(self, data):

        self.send_reply(self.HSC_INFO_OK, 1, __version__)

    def do_vboxwrapper_parser_test(self, data):

        for i in range(len(data)):
            self.send_reply(self.HSC_INFO_MSG, 0,
                            "arg %d (len %u): \"%s\"" % \
                            (i, len(data[i]), data[i])
                            )
        self.send_reply(self.HSC_INFO_OK, 1, "OK")

    def do_vboxwrapper_module_list(self, data):

        for module in self.modules.keys():
            self.send_reply(self.HSC_INFO_MSG, 0, module)
        self.send_reply(self.HSC_INFO_OK, 1, "OK")

    def do_vboxwrapper_cmd_list(self, data):

        module, = data

        if not module in self.modules.keys():
            self.send_reply(self.HSC_ERR_UNK_MODULE, 1,
                            "unknown module '%s'" % module)
            return

        for command in self.modules[module].keys():
            self.send_reply(self.HSC_INFO_MSG, 0,
                            "%s (min/max args: %d/%d)" % \
                            (command,
                             self.modules[module][command][0],
                             self.modules[module][command][1]))

        self.send_reply(self.HSC_INFO_OK, 1, "OK")

    def do_vboxwrapper_working_dir(self, data):

        self.send_reply(self.HSC_INFO_OK, 1, "OK")

        working_dir, = data
        try:
            os.chdir(working_dir)
            global WORKDIR
            WORKDIR = working_dir
            # VBOX doesn't need a working directory ... for now
            #for vbox_name in VBOX_INSTANCES.keys():
            #    VBOX_INSTANCES[vbox_name].workdir = os.path.join(working_dir, VBOX_INSTANCES[vbox_name].name)
            self.send_reply(self.HSC_INFO_OK, 1, "OK")
        except OSError as e:
            self.send_reply(self.HSC_ERR_INV_PARAM, 1, "chdir: %s" % e.strerror)

    def do_vboxwrapper_reset(self, data):

        cleanup()
        self.send_reply(self.HSC_INFO_OK, 1, "OK")

    def do_vboxwrapper_close(self, data):

        self.send_reply(self.HSC_INFO_OK, 1, "OK")
        self.close_connection = 1

    def do_vboxwrapper_stop(self, data):

        self.send_reply(self.HSC_INFO_OK, 1, "OK")
        self.close_connection = 1
        self.server.stop()

    def do_vbox_version(self, data):

        global g_vboxManager, VBOXVER, VBOXVER_REQUIRED

        if g_vboxManager:
            vboxver_maj = VBOXVER.split('.')[0]
            vboxver_min = VBOXVER.split('.')[1]
            vboxver = float(str(vboxver_maj)+'.'+str(vboxver_min))
            if vboxver < VBOXVER_REQUIRED:
                msg = "Detected VirtualBox version %s, which is too old." % VBOXVER + os.linesep + "Minimum required is: %s" % str(VBOXVER_REQUIRED)
                self.send_reply(self.HSC_ERR_BAD_OBJ, 1, msg)
            else:
                self.send_reply(self.HSC_INFO_OK, 1, VBOXVER)
        else:
            if sys.platform == 'win32' and not os.environ.has_key('VBOX_INSTALL_PATH'):
                self.send_reply(self.HSC_ERR_BAD_OBJ, 1, "VirtualBox is not installed.")
            else:
                self.send_reply(self.HSC_ERR_BAD_OBJ, 1, "Failed to load vboxapi, please check your VirtualBox installation.")

    def do_vbox_vm_list(self, data):

        if g_vboxManager:
            try:
                machines = g_vboxManager.getArray(g_vboxManager.vbox, 'machines')
                for ni in range(len(machines)):
                    self.send_reply(self.HSC_INFO_MSG, 0, machines[ni].name)
            except Exception:
                pass
        self.send_reply(self.HSC_INFO_OK, 1, "OK")

    def do_vbox_find_vm(self, data):

        vm_name, = data

        try:
            mach = g_vboxManager.vbox.findMachine(vm_name)
        except Exception:
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1, "unable to find vm %s" % vm_name)
            return

        self.send_reply(self.HSC_INFO_OK, 1, "OK")

    def __vbox_create(self, dev_type, name):

        try:
            devclass = self.vbox_classes[dev_type]
        except KeyError:
            log.error("No device type %s" % dev_type)
            return 1
        if name in VBOX_INSTANCES.keys():
            log.error("Unable to create VBox instance {}, it already exists".format(name))
            return 1

        vbox_instance = devclass(name)

        try:
            vbox_instance.create()
        except OSError as e:
            log.error("Unable to create VBox instance {}".format(name))
            return 1

        VBOX_INSTANCES[name] = vbox_instance
        return 0

    def do_vbox_create(self, data):

        dev_type, name = data
        if self.__vbox_create(dev_type, name) == 0:
            self.send_reply(self.HSC_INFO_OK, 1, "VBox '{}' created".format(name))
        else:
            self.send_reply(self.HSC_ERR_CREATE, 1,
                            "Unable to create VBox instance '{}'".format(name))

    def do_vbox_rename(self, data):

        old_name, new_name = data

        if old_name in VBOX_INSTANCES:
            vbox_instance = VBOX_INSTANCES[old_name]
            vbox_instance.rename(new_name)
            VBOX_INSTANCES[new_name] = VBOX_INSTANCES[old_name]
            del VBOX_INSTANCES[old_name]
            self.send_reply(self.HSC_INFO_OK, 1, "VBox '{}' renamed to '{}'".format(old_name, new_name))
        else:
            self.send_reply(self.HSC_ERR_CREATE, 1,
                            "Unable to rename VBox instance from '{}'".format(old_name))

    def __vbox_delete(self, name):

        if not name in VBOX_INSTANCES.keys():
            return 1
        if VBOX_INSTANCES[name].process and not VBOX_INSTANCES[name].stop():
            return 1
        del VBOX_INSTANCES[name]
        return 0

    def do_vbox_delete(self, data):

        name, = data
        if self.__vbox_delete(name) == 0:
            self.send_reply(self.HSC_INFO_OK, 1, "VBox '%s' deleted" % name)
        else:
            self.send_reply(self.HSC_ERR_DELETE, 1,
                            "unable to delete VBox instance '%s'" % name)

    def do_vbox_setattr(self, data):

        name, attr, value = data
        if value == 'True':
            value = True
        elif value == 'False':
            value = False
        try:
            instance = VBOX_INSTANCES[name]
        except KeyError:
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1,
                             "unable to find VBox '%s'" % name)
            return
        if not attr in instance.valid_attr_names:
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1,
                            "Cannot set attribute '%s' for '%s" % (attr, name))
            return
        log.info("!! {}.{} = {}".format(name, attr, value))
        setattr(VBOX_INSTANCES[name], attr, value)
        self.send_reply(self.HSC_INFO_OK, 1, "%s set for '%s'" % (attr, name))

    def do_vbox_create_nic(self, data):

        #name, vnic, mac = data
        name, vnic = data
        if not name in VBOX_INSTANCES.keys():
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1,
                            "unable to find VBox '%s'" % name)
            return
        #VBOX_INSTANCES[name].nic[int(vnic)] = mac
        self.send_reply(self.HSC_INFO_OK, 1, "OK")

    def do_vbox_create_udp(self, data):

        name, vnic, sport, daddr, dport = data
        if not name in VBOX_INSTANCES.keys():
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1,
                            "unable to find VBox '%s'" % name)
            return
        #Try to create UDP:
        VBOX_INSTANCES[name].create_udp(vnic, sport, daddr, dport)
        #if not VBOX_INSTANCES[name].create_udp(vnic, sport, daddr, dport):
        #    self.send_reply(self.HSC_ERR_CREATE, 1,
        #                    "unable to create UDP connection '%s'" % vnic)
        #    return
        udp_connection = UDPConnection(sport, daddr, dport)
        udp_connection.resolve_names()
        VBOX_INSTANCES[name].udp[int(vnic)] = udp_connection
        self.send_reply(self.HSC_INFO_OK, 1, "OK")

    def do_vbox_delete_udp(self, data):

        name, vnic = data
        if not name in VBOX_INSTANCES.keys():
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1,
                            "unable to find VBox '%s'" % name)
            return
        VBOX_INSTANCES[name].delete_udp(vnic)
        if VBOX_INSTANCES[name].udp.has_key(int(vnic)):
            del VBOX_INSTANCES[name].udp[int(vnic)]
        self.send_reply(self.HSC_INFO_OK, 1, "OK")

    def do_vbox_create_capture(self, data):

        name, vnic, path = data
        if not name in VBOX_INSTANCES.keys():
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1,
                            "unable to find VBox '%s'" % name)
            return

        VBOX_INSTANCES[name].capture[int(vnic)] = path
        self.send_reply(self.HSC_INFO_OK, 1, "OK")

    def do_vbox_delete_capture(self, data):

        name, vnic = data
        if not name in VBOX_INSTANCES.keys():
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1,
                            "unable to find VBox '%s'" % name)
            return
        if VBOX_INSTANCES[name].capture.has_key(int(vnic)):
            del VBOX_INSTANCES[name].capture[int(vnic)]
        self.send_reply(self.HSC_INFO_OK, 1, "OK")

    def do_vbox_start(self, data):

        name, = data
        if not name in VBOX_INSTANCES.keys():
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1,
                            "unable to find VBox '%s'" % name)
            return
        if not VBOX_INSTANCES[name].start():
            self.send_reply(self.HSC_ERR_START, 1,
                            "unable to start instance '%s'" % name)
        else:
            self.send_reply(self.HSC_INFO_OK, 1, "VBox '%s' started" % name)

    def do_vbox_stop(self, data):

        name, = data
        if not VBOX_INSTANCES[name].stop():
            self.send_reply(self.HSC_ERR_STOP, 1,
                            "unable to stop instance '%s'" % name)
        else:
            self.send_reply(self.HSC_INFO_OK, 1, "VBox '%s' stopped" % name)

    def do_vbox_reset(self, data):

        name, = data
        if not VBOX_INSTANCES[name].reset():
            self.send_reply(self.HSC_ERR_STOP, 1,
                            "unable to reset instance '%s'" % name)
        else:
            self.send_reply(self.HSC_INFO_OK, 1, "VBox '%s' rebooted" % name)

    def do_vbox_status(self, data):

        name, = data
        status = VBOX_INSTANCES[name].status()
        self.send_reply(self.HSC_INFO_OK, 1, "%s" % status)

    def do_vbox_suspend(self, data):

        name, = data
        if not VBOX_INSTANCES[name].suspend():
            self.send_reply(self.HSC_ERR_STOP, 1,
                            "unable to suspend instance '%s'" % name)
        else:
            self.send_reply(self.HSC_INFO_OK, 1, "VBox '%s' suspended" % name)

    def do_vbox_resume(self, data):

        name, = data
        if not VBOX_INSTANCES[name].resume():
            self.send_reply(self.HSC_ERR_STOP, 1,
                            "unable to resume instance '%s'" % name)
        else:
            self.send_reply(self.HSC_INFO_OK, 1, "VBox '%s' resumed" % name)

    def do_vbox_clean(self, data):

        name, = data
        if not name in VBOX_INSTANCES.keys():
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1,
                            "unable to find VBox '%s'" % name)
            return
        VBOX_INSTANCES[name].clean()
        self.send_reply(self.HSC_INFO_OK, 1, "OK")

class DaemonThreadingMixIn(SocketServer.ThreadingMixIn):
    daemon_threads = True

#class VBoxWrapperServer(SocketServer.TCPServer):
class VBoxWrapperServer(DaemonThreadingMixIn, SocketServer.TCPServer):
    allow_reuse_address = True

    def __init__(self, server_address, RequestHandlerClass):

        global FORCE_IPV6
        if server_address[0].__contains__(':'):
            FORCE_IPV6 = True
        if FORCE_IPV6:
            # IPv6 address support
            self.address_family = socket.AF_INET6
        try:
            SocketServer.TCPServer.__init__(self, server_address, RequestHandlerClass)
        except socket.error as e:
            log.critical("{}".format(e))
            sys.exit(1)
        self.stopping = threading.Event()
        self.pause = 0.1

    def serve_forever(self):
        while not self.stopping.isSet():
            if select.select([self.socket], [], [], self.pause)[0]:
                self.handle_request()
        cleanup()

    def stop(self):
        self.stopping.set()

def cleanup():
    print("Shutdown in progress...")
    for name in VBOX_INSTANCES.keys():
        if VBOX_INSTANCES[name].process:
            VBOX_INSTANCES[name].stop()
        del VBOX_INSTANCES[name]
    print("Shutdown completed.")

def main():

    global IP
    from optparse import OptionParser

    print("VirtualBox Wrapper (version %s)" % __version__)
    print("Copyright (c) 2007-2014")
    print("Jeremy Grossmann and Alexey Eromenko")

    if sys.platform == 'win32':
        try:
            import win32com, pythoncom
        except ImportError:
            log.critical("You need pywin32 installed to run vboxwrapper!")
            sys.exit(1)

    usage = "usage: %prog [--listen <ip_address>] [--port <port_number>] [--forceipv6 true]"
    parser = OptionParser(usage, version="%prog " + __version__)
    parser.add_option("-l", "--listen", dest="host", help="IP address or hostname to listen on (default is to listen on all interfaces)")
    parser.add_option("-p", "--port", type="int", dest="port", help="Port number (default is 11525)")
    parser.add_option("-w", "--workdir", dest="wd", help="Working directory (default is current directory)")
    parser.add_option("-6", "--forceipv6", dest="force_ipv6", help="Force IPv6 usage (default is false; i.e. IPv4)")
    parser.add_option("-n", "--no-vbox-checks", action="store_true", dest="no_vbox_checks", default=False, help="Do not check for vboxapi loading and VirtualBox version")

    # ignore an option automatically given by Py2App
    if sys.platform.startswith('darwin') and len(sys.argv) > 1 and sys.argv[1].startswith("-psn"):
        del sys.argv[1]

    try:
        # trick to ignore an option automatically given by Py2App
        #if sys.platform.startswith('darwin') and hasattr(sys, "frozen"):
        #    (options, args) = parser.parse_args(sys.argv[2:])
        #else:
        (options, args) = parser.parse_args()
    except SystemExit:
        sys.exit(1)

    global g_vboxManager, VBOXVER, VBOXVER_REQUIRED, VBOX_STREAM
    if not options.no_vbox_checks and not g_vboxManager:
        log.critical("ERROR: vboxapi module cannot be loaded" + os.linesep + "Please check your VirtualBox installation.")
        sys.exit(1)

    if g_vboxManager:
        VBOXVER = g_vboxManager.vbox.version
        print("Using VirtualBox %s r%d" % (VBOXVER, g_vboxManager.vbox.revision))

        if not options.no_vbox_checks:
            vboxver_maj = VBOXVER.split('.')[0]
            vboxver_min = VBOXVER.split('.')[1]
            vboxver = float(str(vboxver_maj)+'.'+str(vboxver_min))
            if vboxver < VBOXVER_REQUIRED:
                log.critical("ERROR: Detected VirtualBox version %s, which is too old." % VBOXVER + os.linesep + "Minimum required is: %s" % str(VBOXVER_REQUIRED))
                sys.exit(1)

        if sys.platform == 'win32':
            VBOX_STREAM = pythoncom.CoMarshalInterThreadInterfaceInStream(pythoncom.IID_IDispatch, g_vboxManager.vbox)

    if options.host and options.host != '0.0.0.0':
        host = options.host
        global IP
        IP = host
    else:
        host = IP

    if options.port:
        port = options.port
        global PORT
        PORT = port
    else:
        port = PORT

    if options.wd:
        global WORKDIR
        WORKDIR = options.wd

    if options.force_ipv6 and not (options.force_ipv6.lower().__contains__("false") or options.force_ipv6.__contains__("0")):
        global FORCE_IPV6
        FORCE_IPV6 = options.force_ipv6

    server = VBoxWrapperServer((host, port), VBoxWrapperRequestHandler)

    print("VBoxWrapper TCP control server started (port %d)." % port)


    if FORCE_IPV6:
        LISTENING_MODE = "Listening in IPv6 mode"
    else:
        LISTENING_MODE = "Listening"

    if IP:
        print("%s on %s" % (LISTENING_MODE, IP))
    else:
        print("%s on all network interfaces" % LISTENING_MODE)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        cleanup()


if __name__ == '__main__':
    main()
