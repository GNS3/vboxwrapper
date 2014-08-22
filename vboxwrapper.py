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
This module is used for actual control of VMs, sending commands to VBox controllers.
VBox controllers implement VirtualBox version-specific API calls.
This is the server part, it can be started manually or automatically by GNS3.
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

from optparse import OptionParser
from virtualbox_controller import VirtualBoxController
from virtualbox_error import VirtualBoxError
from adapters.ethernet_adapter import EthernetAdapter
from nios.nio_udp import NIO_UDP

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
__version__ = '0.9.1'

PORT = 11525
IP = ""
VBOX_INSTANCES = {}
FORCE_IPV6 = False
VBOX_STREAM = 0
VBOXVER = 0.0
VBOXVER_REQUIRED = 4.1
VBOX_MANAGER = 0

try:
    from vboxapi import VirtualBoxManager
    VBOX_MANAGER = VirtualBoxManager(None, None)
except:
    pass


class UDPConnection:
    """
    Stores UDP connection info.
    """

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


class VBOXInstance:
    """
    Represents a VirtualBox instance.
    """

    def __init__(self, name):

        self.name = name
        self.console = ''
        self.image = ''
        self.nic = {}
        self.nics = '6'
        self.udp = {}
        self.capture = {}
        self.netcard = 'Automatic'
        self.headless_mode = False
        self.process = None
        self.pipeThread = None
        self.pipe = None
        self._vboxcontroller = None
        self._ethernet_adapters = []
        self.valid_attr_names = ['image',
                                 'console',
                                 'nics',
                                 'netcard',
                                 'headless_mode']

    def _start_vbox_service(self, vmname):

        global VBOX_STREAM, VBOX_MANAGER, IP

        # Initialize the controller
        vbox_manager = VBOX_MANAGER
        self._vboxcontroller = VirtualBoxController(vmname, vbox_manager, IP)

        # Initialize win32 COM
        if sys.platform == 'win32':
            # Microsoft COM behaves differently than Mozilla XPCOM, and requires special multi-threading code.
            # Get the VBox interface from previous thread.
            i = pythoncom.CoGetInterfaceAndReleaseStream(VBOX_STREAM, pythoncom.IID_IDispatch)
            vbox_manager.vbox = win32com.client.Dispatch(i)
            VBOX_STREAM = pythoncom.CoMarshalInterThreadInterfaceInStream(pythoncom.IID_IDispatch, vbox_manager.vbox)

    def start(self):
        """
        Starts this instance.
        """

        log.debug("{}: start".format(self.name))
        vmname = self.image

        if not self._vboxcontroller:
            self._start_vbox_service(vmname)

        # glue
        self._vboxcontroller.console = int(self.console)
        self._vboxcontroller.adapter_type = self.netcard
        self._vboxcontroller.headless = self.headless_mode
        self._ethernet_adapters = []
        for adapter_id in range(0, int(self.nics)):
            adapter = EthernetAdapter()
            if adapter_id in self.udp:
                udp_info = self.udp[adapter_id]
                nio = NIO_UDP(udp_info.lport, udp_info.rhost, udp_info.rport)
                if adapter_id in self.capture:
                    capture_file = self.capture[adapter_id]
                    nio.startPacketCapture(capture_file)
                adapter.add_nio(0, nio)
            self._ethernet_adapters.append(adapter)
        self._vboxcontroller.adapters = self._ethernet_adapters

        try:
            self._vboxcontroller.start()
        except VirtualBoxError as e:
            log.error(e)
            return False
        return True

    def reset(self):
        """
        Resets this instance.
        """

        log.debug("{}: reset".format(self.name))
        try:
            if not self._vboxcontroller:
                return True
            self._vboxcontroller.reload()
        except VirtualBoxError as e:
            log.error(e)
            return False
        return True

    def stop(self):
        """
        Stops this instance.
        """

        log.debug("{}: stop".format(self.name))
        try:
            if not self._vboxcontroller:
                return True
            self._vboxcontroller.stop()
        except VirtualBoxError as e:
            log.error(e)
            return False
        return True

    def suspend(self):
        """
        Suspends this instance.
        """

        log.debug("{}: suspend".format(self.name))
        try:
            if not self._vboxcontroller:
                return True
            self._vboxcontroller.suspend()
        except VirtualBoxError as e:
            log.error(e)
            return False
        return True

    def rename(self, new_name):
        """
        Renames this instance.
        """

        log.debug("{}: rename".format(self.name))
        self.name = new_name

    def resume(self):
        """
        Resumes this instance.
        """

        log.debug("{}: resume".format(self.name))
        try:
            if not self._vboxcontroller:
                return True
            self._vboxcontroller.resume()
        except VirtualBoxError as e:
            log.error(e)
            return False
        return True

    def create_udp(self, i_vnic, sport, daddr, dport):
        """
        Creates an UDP tunnel.
        """

        log.debug("{}: create_udp".format(self.name))
        try:
            if not self._vboxcontroller:
                return True
            self._vboxcontroller.create_udp(int(i_vnic), sport, daddr, dport)
        except VirtualBoxError as e:
            log.error(e)
            return False
        return True

    def delete_udp(self, i_vnic):
        """
        Deletes an UDP tunnel.
        """

        log.debug("{}: delete_udp".format(self.name))
        try:
            if not self._vboxcontroller:
                return True
            self._vboxcontroller.delete_udp(int(i_vnic))
        except VirtualBoxError as e:
            log.error(e)
            return False
        return True

class VBoxWrapperRequestHandler(SocketServer.StreamRequestHandler):
    """
    Handles requests.
    """

    modules = {
        'vboxwrapper': {
            'version': (0, 0),
            'reset': (0, 0),
            'close': (0, 0),
            'stop': (0, 0),
            },
        'vbox' : {
            'version': (0, 0),
            'vm_list': (0, 0),
            'find_vm': (1, 1),
            'rename': (2, 2),
            'create': (2, 2),
            'delete': (1, 1),
            'setattr': (3, 3),
            'create_udp': (5, 5),
            'delete_udp': (2, 2),
            'create_capture': (3, 3),
            'delete_capture': (2, 2),
            'start': (1, 1),
            'stop': (1, 1),
            'reset': (1, 1),
            'suspend': (1, 1),
            'resume': (1, 1),
            'clean': (1, 1),
            }
        }

    vbox_classes = {
        'vbox': VBOXInstance,
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

    def handle(self):
        """
        Handles a client connection.
        """

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
        """
        Tokenize a request.
        """

        input_ = cStringIO.StringIO(request)
        tokens = []
        try:
            tokens = csv.reader(input_, delimiter=' ').next()
        except StopIteration:
            pass
        return tokens

    def finish(self):
        """
        Handles a client disconnection.
        """

        pass

    def handle_one_request(self):
        """
        Handles one request.
        """

        request = self.rfile.readline()

        # Don't process empty strings (this creates Broken Pipe exceptions)
        # FIXME: this causes 100% cpu usage on Windows.
        #if request == "":
        #    return

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
            if len(data) < self.modules[module][command][0] or len(data) > self.modules[module][command][1]:
                self.send_reply(self.HSC_ERR_BAD_PARAM, 1,
                                "Bad number of parameters (%d with min/max=%d/%d)" % (len(data),
                                                                                      self.modules[module][command][0],
                                                                                      self.modules[module][command][1]))
                return
        except Exception as e:
            # This can happen, if you add send command, but forget to define it in class modules
            self.send_reply(self.HSC_ERR_INV_PARAM, 1, "Unknown Exception")
            log.error("exception in handle_one_request(): {}".format(e))
            return

        # Call the function.
        method = getattr(self, mname)
        method(data)

    def send_reply(self, code, done, msg):
        """
        Sends a reply.
        """

        sep = '-'
        if not done:
            sep = ' '
        reply = "%3d%s%s\r\n" % (code, sep, msg)
        self.wfile.write(reply)

    def do_vboxwrapper_version(self, data):
        """
        Handles the vboxwrapper version command.
        """

        self.send_reply(self.HSC_INFO_OK, 1, __version__)

    def do_vboxwrapper_reset(self, data):
        """
        Handles the vboxwrapper reset command.
        """

        cleanup()
        self.send_reply(self.HSC_INFO_OK, 1, "OK")

    def do_vboxwrapper_close(self, data):
        """
        Handles the vboxwrapper close command.
        """

        self.send_reply(self.HSC_INFO_OK, 1, "OK")
        self.close_connection = 1

    def do_vboxwrapper_stop(self, data):
        """
        Handles the vboxwrapper stop command.
        """

        self.send_reply(self.HSC_INFO_OK, 1, "OK")
        self.close_connection = 1
        self.server.stop()

    def do_vbox_version(self, data):
        """
        Handles the vbox version command.
        """

        global VBOX_MANAGER, VBOXVER, VBOXVER_REQUIRED

        if VBOX_MANAGER:
            vboxver_maj = VBOXVER.split('.')[0]
            vboxver_min = VBOXVER.split('.')[1]
            vboxver = float(str(vboxver_maj)+'.'+str(vboxver_min))
            if vboxver < VBOXVER_REQUIRED:
                msg = "Detected VirtualBox version %s, which is too old." % VBOXVER + os.linesep + "Minimum required is: %s" % str(VBOXVER_REQUIRED)
                self.send_reply(self.HSC_ERR_BAD_OBJ, 1, msg)
            else:
                self.send_reply(self.HSC_INFO_OK, 1, VBOXVER)
        else:
            if sys.platform.startswith("win") and not os.environ.has_key('VBOX_INSTALL_PATH'):
                self.send_reply(self.HSC_ERR_BAD_OBJ, 1, "VirtualBox is not installed.")
            else:
                self.send_reply(self.HSC_ERR_BAD_OBJ, 1, "Failed to load vboxapi, please check your VirtualBox installation.")

    def do_vbox_vm_list(self, data):
        """
        Handles the vbox vm_list command.
        """

        if VBOX_MANAGER:
            try:
                machines = VBOX_MANAGER.getArray(VBOX_MANAGER.vbox, 'machines')
                for ni in range(len(machines)):
                    self.send_reply(self.HSC_INFO_MSG, 0, machines[ni].name)
            except Exception:
                pass
        self.send_reply(self.HSC_INFO_OK, 1, "OK")

    def do_vbox_find_vm(self, data):
        """
        Handles the vbox find_vm command.
        """

        vm_name, = data
        try:
            VBOX_MANAGER.vbox.findMachine(vm_name)
        except Exception:
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1, "unable to find vm %s" % vm_name)
            return
        self.send_reply(self.HSC_INFO_OK, 1, "OK")

    def __vbox_create(self, dev_type, name):
        """
        Creates a new vbox instance.
        """

        try:
            devclass = self.vbox_classes[dev_type]
        except KeyError:
            log.error("No device type %s" % dev_type)
            return 1
        if name in VBOX_INSTANCES.keys():
            log.error("Unable to create VBox instance {}, it already exists".format(name))
            return 1

        VBOX_INSTANCES[name] = devclass(name)
        return 0

    def do_vbox_create(self, data):
        """
        Handles the vbox create command.
        """

        dev_type, name = data
        if self.__vbox_create(dev_type, name) == 0:
            self.send_reply(self.HSC_INFO_OK, 1, "VBox '{}' created".format(name))
        else:
            self.send_reply(self.HSC_ERR_CREATE, 1,
                            "Unable to create VBox instance '{}'".format(name))

    def do_vbox_rename(self, data):
        """
        Handles the vbox rename command.
        """

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
        """
        Deletes a vbox instance.
        """

        if not name in VBOX_INSTANCES.keys():
            return 1
        if VBOX_INSTANCES[name].process and not VBOX_INSTANCES[name].stop():
            return 1
        del VBOX_INSTANCES[name]
        return 0

    def do_vbox_delete(self, data):
        """
        Handles the vbox delete command.
        """

        name, = data
        if self.__vbox_delete(name) == 0:
            self.send_reply(self.HSC_INFO_OK, 1, "VBox '%s' deleted" % name)
        else:
            self.send_reply(self.HSC_ERR_DELETE, 1,
                            "unable to delete VBox instance '%s'" % name)

    def do_vbox_setattr(self, data):
        """
        Handles the setattr command.
        """

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

    def do_vbox_create_udp(self, data):
        """
        Handles the create udp command.
        """

        name, vnic, sport, daddr, dport = data
        if not name in VBOX_INSTANCES.keys():
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1,
                            "unable to find VBox '%s'" % name)
            return
        VBOX_INSTANCES[name].create_udp(vnic, sport, daddr, dport)
        udp_connection = UDPConnection(sport, daddr, dport)
        udp_connection.resolve_names()
        VBOX_INSTANCES[name].udp[int(vnic)] = udp_connection
        self.send_reply(self.HSC_INFO_OK, 1, "OK")

    def do_vbox_delete_udp(self, data):
        """
        Handles the delete udp command.
        """

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
        """
        Handles the create capture command.
        """

        name, vnic, path = data
        if not name in VBOX_INSTANCES.keys():
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1,
                            "unable to find VBox '%s'" % name)
            return

        VBOX_INSTANCES[name].capture[int(vnic)] = path
        self.send_reply(self.HSC_INFO_OK, 1, "OK")

    def do_vbox_delete_capture(self, data):
        """
        Handles the delete capture command.
        """

        name, vnic = data
        if not name in VBOX_INSTANCES.keys():
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1,
                            "unable to find VBox '%s'" % name)
            return
        if VBOX_INSTANCES[name].capture.has_key(int(vnic)):
            del VBOX_INSTANCES[name].capture[int(vnic)]
        self.send_reply(self.HSC_INFO_OK, 1, "OK")

    def do_vbox_start(self, data):
        """
        Handles the start command.
        """

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
        """
        Handles the stop command.
        """

        name, = data
        if not name in VBOX_INSTANCES.keys():
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1,
                            "unable to find VBox '%s'" % name)
            return
        if not VBOX_INSTANCES[name].stop():
            self.send_reply(self.HSC_ERR_STOP, 1,
                            "unable to stop instance '%s'" % name)
        else:
            self.send_reply(self.HSC_INFO_OK, 1, "VBox '%s' stopped" % name)

    def do_vbox_reset(self, data):
        """
        Handles the reset command.
        """

        name, = data
        if not name in VBOX_INSTANCES.keys():
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1,
                            "unable to find VBox '%s'" % name)
            return
        if not VBOX_INSTANCES[name].reset():
            self.send_reply(self.HSC_ERR_STOP, 1,
                            "unable to reset instance '%s'" % name)
        else:
            self.send_reply(self.HSC_INFO_OK, 1, "VBox '%s' rebooted" % name)

    def do_vbox_suspend(self, data):
        """
        Handles the suspend command.
        """

        name, = data
        if not name in VBOX_INSTANCES.keys():
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1,
                            "unable to find VBox '%s'" % name)
            return
        if not VBOX_INSTANCES[name].suspend():
            self.send_reply(self.HSC_ERR_STOP, 1,
                            "unable to suspend instance '%s'" % name)
        else:
            self.send_reply(self.HSC_INFO_OK, 1, "VBox '%s' suspended" % name)

    def do_vbox_resume(self, data):
        """
        Handles the resume command.
        """

        name, = data
        if not name in VBOX_INSTANCES.keys():
            self.send_reply(self.HSC_ERR_UNK_OBJ, 1,
                            "unable to find VBox '%s'" % name)
            return
        if not VBOX_INSTANCES[name].resume():
            self.send_reply(self.HSC_ERR_STOP, 1,
                            "unable to resume instance '%s'" % name)
        else:
            self.send_reply(self.HSC_INFO_OK, 1, "VBox '%s' resumed" % name)


class DaemonThreadingMixIn(SocketServer.ThreadingMixIn):
    """
    Defines attributes for the Multi-threaded TCP server.
    """

    daemon_threads = True


class VBoxWrapperServer(DaemonThreadingMixIn, SocketServer.TCPServer):
    """
    Multi-threaded TCP server.
    """

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
    """
    Stops and deletes all VirtualBox instances.
    """

    print("Shutdown in progress...")
    for name in VBOX_INSTANCES.keys():
        if VBOX_INSTANCES[name].process:
            VBOX_INSTANCES[name].stop()
        del VBOX_INSTANCES[name]
    print("Shutdown completed.")


def main():
    """
    VirtualBox wrapper entry point.
    """

    global IP
    print("VirtualBox Wrapper (version %s)" % __version__)
    print("Copyright (c) 2007-2014")
    print("Jeremy Grossmann and Alexey Eromenko")

    if sys.platform.startswith("win"):
        try:
            import win32com
            import pythoncom
        except ImportError:
            print("pywin32 and pythoncom modules must be installed.", file=sys.stderr)
            sys.exit(1)

    usage = "usage: %prog [--listen <ip_address>] [--port <port_number>] [--forceipv6 true]"
    parser = OptionParser(usage, version="%prog " + __version__)
    parser.add_option("-l", "--listen", dest="host", help="IP address or hostname to listen on (default is to listen on all interfaces)")
    parser.add_option("-p", "--port", type="int", dest="port", help="Port number (default is 11525)")
    parser.add_option("-6", "--forceipv6", dest="force_ipv6", help="Force IPv6 usage (default is false; i.e. IPv4)")
    parser.add_option("-n", "--no-vbox-checks", action="store_true", dest="no_vbox_checks", default=False, help="Do not check for vboxapi and VirtualBox version")

    # ignore an option automatically given by Py2App
    if sys.platform.startswith("darwin") and len(sys.argv) > 1 and sys.argv[1].startswith("-psn"):
        del sys.argv[1]

    try:
        options, args = parser.parse_args()
    except SystemExit:
        sys.exit(1)

    global VBOX_MANAGER, VBOXVER, VBOXVER_REQUIRED, VBOX_STREAM

    if not options.no_vbox_checks and not VBOX_MANAGER:
        print("vboxapi module cannot be loaded, please check if VirtualBox is correctly installed.", file=sys.stderr)
        sys.exit(1)

    if VBOX_MANAGER:
        VBOXVER = VBOX_MANAGER.vbox.version
        print("Using VirtualBox %s r%d" % (VBOXVER, VBOX_MANAGER.vbox.revision))

        if not options.no_vbox_checks:
            vboxver_maj = VBOXVER.split('.')[0]
            vboxver_min = VBOXVER.split('.')[1]
            vboxver = float(str(vboxver_maj) + '.' + str(vboxver_min))
            if vboxver < VBOXVER_REQUIRED:
                print("detected version of VirtualBox is {}, which is too old. Minimum required is {}.".format(VBOXVER, VBOXVER_REQUIRED), file=sys.stderr)
                sys.exit(1)

        if sys.platform.startswith("win32"):
            VBOX_STREAM = pythoncom.CoMarshalInterThreadInterfaceInStream(pythoncom.IID_IDispatch, VBOX_MANAGER.vbox)

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
