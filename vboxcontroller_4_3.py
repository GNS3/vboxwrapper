#!/usr/bin/env python
# vim: expandtab ts=4 sw=4 sts=4 fileencoding=utf-8:
#
# Copyright (c) 2014 Jeremy Grossmann & Alexey Eromenko "Technologov"
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
This module is used for actual control of the VirtualBox 4.3 hypervisor.

This module is separate from actual vboxwrapper code, because VirtualBox
breaks API compatibility with every major release, so if we are to support
several different major versions of VirtualBox, several controllers will need
to be written. Essentially this module makes vboxwrapper future-proof.
"""

from __future__ import print_function

import time
import sys
import subprocess as sub

import logging
log = logging.getLogger(__name__)

"""
Basic VirtualBox initialization commands: (provided as example)

from vboxapi import VirtualBoxManager
mgr = VirtualBoxManager(None, None)
vbox = mgr.vbox
name = "My VM name"
mach = vbox.findMachine(name)
session = mgr.mgr.getSessionObject(vbox)

progress = mach.launchVMProcess(session, "gui", "")
progress.waitForCompletion(-1)

console=session.console
"""

class VBoxController_4_3(object):

    def __init__(self, io_vboxManager):

        log.debug("VboxController_4_3 is initializing")
        self.mgr = io_vboxManager
        self.vbox = self.mgr.vbox
        self.maxNics = 8
        self.constants = self.mgr.constants
        self.statBytesReceived = 0
        self.statBytesSent = 0
        self.stats = ""
        self.guestIP = ""
        self.VBoxBug9239Workaround = True # VBoxSVC crash on Windows hosts.
        self.mach = None

    def start(self, vmname, nics, udp, capture, netcard, headless_mode=False, pipe_name=None):

        log.info("starting VM {}".format(vmname))

        self.vmname = vmname
        self.nics = nics
        self.udp = udp
        self.capture = capture
        self.netcard = netcard
        self.headless_mode = headless_mode
        self.pipe_name = pipe_name

        try:
            self.mach = self.vbox.findMachine(self.vmname)
        except Exception as e:
            # this usually happens if you try to start non-existent or unregistered VM
            log.error("could not find VM {}: {}".format(self.vmname, e))
            return False

        if self.mach.state >= self.constants.MachineState_FirstOnline and self.mach.state <= self.constants.MachineState_LastOnline:
            # the machine is being executed
            return False

        # The maximum support network cards depends on the Chipset (PIIX3 or ICH9)
        self.maxNics = self.vbox.systemProperties.getMaxNetworkAdapters(self.mach.chipsetType)

        if not self._safeGetSessionObject():
            return False
        if not self._safeNetOptions():
            return False
        if not self._safeConsoleOptions():
            return False
        if not self._safeLaunchVMProcess():
            return False

        log.info("VM is starting with {}% completed".format(self.progress.percent))
        if self.progress.percent != 100:
            # This will happen if you attempt to start VirtualBox with unloaded "vboxdrv" module.
            # or have too little RAM or damaged vHDD, or connected to non-existent network.
            # We must unlock machine, otherwise it locks the VirtualBox Manager GUI. (on Linux hosts)
            self._safeUnlockMachine()
            return False
        try:
            self.console = self.session.console
        except Exception as e:
            log.error("could not get the console session for {}: {}".format(self.vmname, e))
            return False
        return True

    def status(self):

        if not self.mach:
            return 0
        return self.mach.state

    def reset(self):

        log.info("resetting VM {}".format(self.vmname))
        try:
            self.progress = self.console.reset()
            self.progress.waitForCompletion(-1)
        except Exception as e:
            # Do not crash "vboxwrapper", if stopping VM fails.
            # But return True anyway, so VM state in GNS3 can become "stopped"
            # This can happen, if user manually kills VBox VM.
            log.warn("could not reset the VM {}: {}".format(self.vmname, e))
            return True

    def stop(self):

        if self.VBoxBug9239Workaround and sys.platform == 'win32':
            p = sub.Popen('cd /D "%%VBOX_INSTALL_PATH%%" && VBoxManage.exe controlvm "%s" poweroff' % self.vmname, shell=True)
            p.communicate()
        else:
            try:
                self.progress = self.console.powerDown()
                # wait for VM to actually go down
                self.progress.waitForCompletion(-1)
                log.info("VM is stopping with {}% completed".format(self.vmname, self.progress.percent))
            except Exception:
                # Do not crash "vboxwrapper", if stopping VM fails.
                # But return True anyway, so VM state in GNS3 can become "stopped"
                # This can happen, if user manually kills VBox VM.
                return True

        # shutdown all managed interfaces
        if not self._safeLockMachine():
            return True
        try:
            mach2 = self.session.machine
        except Exception as e:
            log.error("could not get the machine session, skipping shutdown of interfaces: {}".format(e))
            return True

        for vnic in range(0, int(self.nics)):
            if not self._safeDisableNetAdpFromMachine(mach2, vnic, disableAdapter=True):
                # return True anyway, so VM state in GNS3 can become "stopped"
                return True

        self._safeSaveSettings(mach2)  # doesn't matter if command returns True or False...
        self._safeUnlockMachine()  # doesn't matter if command returns True or False...
        return True

    def suspend(self):

        log.info("suspending VM {}".format(self.vmname))
        try:
            self.console.pause()
        except:
            return False
        return True

    def resume(self):

        log.info("resuming VM {}".format(self.vmname))
        if self.mach.state != self.constants.MachineState_Paused:
            return False

        try:
            self.console.resume()
        except:
            return False
        return True
    
    def setName(self, name):

        log.info("setting name for VM {}".format(self.vmname))
        try:
            self.mach.setGuestPropertyValue("NameInGNS3", name)
        except Exception:
            pass
        # except E_ACCESSDENIED:
        #     #debugmsg(2, "setName FAILED : E_ACCESSDENIED")
        #     return False
        # except VBOX_E_INVALID_VM_STATE:
        #     #debugmsg(2, "setName FAILED : VBOX_E_INVALID_VM_STATE")
        #     return False
        # except VBOX_E_INVALID_OBJECT_STATE:
        #     #debugmsg(2, "setName FAILED : VBOX_E_INVALID_OBJECT_STATE")
        #     return False
        return True

    def create_udp(self, i_vnic, sport, daddr, dport):

        if not self.mach:
            return True

        if self.mach.state >= self.constants.MachineState_FirstOnline and \
                self.mach.state <= self.constants.MachineState_LastOnline:
            retries = 4
            for retry in range(retries):
                if retry == (retries - 1):
                    log.error("could not create an UDP tunnel after 4 retries")
                    return False
                try:
                    mach2 = self.session.machine
                    netadp = mach2.getNetworkAdapter(int(i_vnic))
                    netadp.cableConnected = True
                    netadp.attachmentType = self.constants.NetworkAttachmentType_Null
                    mach2.saveSettings()
                    netadp.attachmentType = self.constants.NetworkAttachmentType_Generic
                    netadp.genericDriver = "UDPTunnel"
                    netadp.setProperty("sport", str(sport))
                    netadp.setProperty("dest", daddr)
                    netadp.setProperty("dport", str(dport))
                    mach2.saveSettings()
                    break
                except Exception as e:
                    # usually due to COM Error: "The object is not ready"
                    log.warn("cannot create UDP tunnel: {}".format(e))
                    time.sleep(0.75)
                    continue
            return True

    def delete_udp(self, i_vnic):

        if not self.mach:
            return True

        if self.mach.state >= self.constants.MachineState_FirstOnline and \
                self.mach.state <= self.constants.MachineState_LastOnline:
            retries = 4
            for retry in range(retries):
                if retry == (retries - 1):
                    log.error("could not delete an UDP tunnel after 4 retries")
                    return False
                try:
                    mach2 = self.session.machine
                    netadp = mach2.getNetworkAdapter(int(i_vnic))
                    netadp.attachmentType = self.constants.NetworkAttachmentType_Null
                    netadp.cableConnected = False
                    mach2.saveSettings()
                    break
                except Exception as e:
                    # usually due to COM Error: "The object is not ready"
                    log.warn("cannot delete UDP tunnel: {}".format(e))
                    time.sleep(0.75)
                    continue
            return True

    def _console_options(self):
        """
        # Example to manually set serial parameters using Python

        from vboxapi import VirtualBoxManager
        mgr = VirtualBoxManager(None, None)
        mach = mgr.vbox.findMachine("My VM")
        session = mgr.mgr.getSessionObject(mgr.vbox)
        mach.lockMachine(session, 1)
        mach2=session.machine
        serial_port = mach2.getSerialPort(0)
        serial_port.enabled = True
        serial_port.path = "/tmp/test_pipe"
        serial_port.hostMode = 1
        serial_port.server = True
        session.unlockMachine()
        """

        log.info("setting console options for {}".format(self.vmname))

        # This code looks really ulgy due to constant 'try' and 'except' pairs.
        # But this is because VirtualBox COM interfaces constantly fails
        # on slow or loaded hosts. (on both Windows and Linux hosts)
        # Without 'try/except' pairs it results in vboxwrapper crashes.
        # To reproduce: Try to configure several VMs, and restart them all in
        # loop on heavily loaded hosts.

        if not self._safeLockMachine():
            return False
        try:
            mach2 = self.session.machine
        except Exception as e:
            log.error("could not get the console session for {}: {}".format(self.vmname, e))
            return False

        try:
            serial_port = mach2.getSerialPort(0)
            if self.pipe_name:
                serial_port.enabled = True
                serial_port.path = self.pipe_name
                serial_port.hostMode = 1
                serial_port.server = True
            else:
                serial_port.enabled = False
        except Exception as e:
            # usually due to COM Error: "The object is not ready"
            log.error("could not set the console options for {}: {}".format(self.vmname, e))
            return False

        if not self._safeSaveSettings(mach2):
            return False
        if not self._safeUnlockMachine():
            return False
        return True

    def _net_options(self):

        log.info("setting network options for {}".format(self.vmname))

        #This code looks really ulgy due to constant 'try' and 'except' pairs.
        #But this is because VirtualBox COM interfaces constantly fails
        #  on slow or loaded hosts. (on both Windows and Linux hosts)
        #Without 'try/except' pairs it results in vboxwrapper crashes.
        #
        #To reproduce: Try to configure several VMs, and restart them all in
        #  loop on heavily loaded hosts.

        if not self._safeLockMachine():
            return False
        try:
            mach2 = self.session.machine
        except Exception as e:
            log.error("could not get the console session for {}: {}".format(self.vmname, e))
            return False

        adaptertype_mgmt = self.constants.NetworkAdapterType_I82540EM
        try:
            netadp_mgmt = mach2.getNetworkAdapter(0)
            adaptertype_mgmt = netadp_mgmt.adapterType
        except Exception as e:
            # usually due to COM Error: "The object is not ready"
            log.error("could not set the network options for {}: {}".format(self.vmname, e))
            #return False

        for vnic in range(0, int(self.nics)):
            try:
                # Vbox API starts counting from 0
                netadp = mach2.getNetworkAdapter(vnic)
                adaptertype = netadp.adapterType

                if self.netcard == "PCnet-PCI II (Am79C970A)":
                    adaptertype = self.constants.NetworkAdapterType_Am79C970A
                if self.netcard == "PCNet-FAST III (Am79C973)":
                    adaptertype = self.constants.NetworkAdapterType_Am79C973
                if self.netcard == "Intel PRO/1000 MT Desktop (82540EM)":
                    adaptertype = self.constants.NetworkAdapterType_I82540EM
                if self.netcard == "Intel PRO/1000 T Server (82543GC)":
                    adaptertype = self.constants.NetworkAdapterType_I82543GC
                if self.netcard == "Intel PRO/1000 MT Server (82545EM)":
                    adaptertype = self.constants.NetworkAdapterType_I82545EM
                if self.netcard == "Paravirtualized Network (virtio-net)":
                    adaptertype = self.constants.NetworkAdapterType_Virtio
                if self.netcard == "Automatic":  # "Auto-guess, based on first NIC"
                    adaptertype = adaptertype_mgmt

                netadp.adapterType = adaptertype

            except Exception as e:
                # usually due to COM Error on loaded hosts: "The object is not ready"
                log.error("could not set the network options for {}: {}".format(self.vmname, e))
                return False

            if vnic in self.udp:
                log.debug("changing netadp mode for VNIC {}".format(vnic))
                try:
                    netadp.enabled = True
                    netadp.cableConnected = True
                    # Temporary hack around VBox-UDP patch limitation: inability to use DNS
                    if str(self.udp[vnic].rhost) == 'localhost':
                        daddr = '127.0.0.1'
                    else:
                        daddr = str(self.udp[vnic].rhost)
                    netadp.attachmentType = self.constants.NetworkAttachmentType_Generic
                    netadp.genericDriver = "UDPTunnel"
                    netadp.setProperty("sport", str(self.udp[vnic].lport))
                    netadp.setProperty("dest", daddr)
                    netadp.setProperty("dport", str(self.udp[vnic].rport))
                except Exception as e:
                    # usually due to COM Error: "The object is not ready"
                    log.error("could not set the network options for {}: {}".format(self.vmname, e))
                    return False
            else:
                # shutting down unused interfaces... vNICs <2-N>
                if not self._safeDetachNetAdp(netadp):
                    return False

            if vnic in self.capture:
                if not self._safeEnableCapture(netadp, self.capture[vnic]):
                    return False

        #for vnic in range(int(self.nics), self.maxNics):
        #    log.debug("disabling remaining VNIC {}".format(vnic))
        #    if not self._safeDisableNetAdpFromMachine(mach2, vnic):
        #        return False

        if not self._safeSaveSettings(mach2):
            return False
        if not self._safeUnlockMachine():
            return False
        return True

    def _safeEnableCapture(self, i_netadp, i_filename):

        # _safe*() functions exist as a protection against COM failure on loaded hosts.
        log.debug("enabling capture for {}".format(self.vmname))
        # this command is retried several times, because it fails more often...
        retries = 4
        for retry in range(retries):
            if retry == (retries - 1):
                log.error("could not enable packet capture after 4 retries for {}".format(self.vmname))
                return False
            try:
                i_netadp.traceEnabled = True
                i_netadp.traceFile = i_filename
                break
            except Exception as e:
                log.warn("cannot enable packet capture for {}, retrying {}: {}".format(self.vmname, retry + 1, e))
                time.sleep(0.75)
                continue
        return True

    def _safeLaunchVMProcess(self):

        # _safe*() functions exist as a protection against COM failure on loaded hosts.
        log.debug("launching VM {}".format(self.vmname))
        # this command is retried several times, because it fails more often...
        retries = 4
        for retry in range(retries):
            if retry == (retries - 1):
                log.error("could not launch the VM after 4 retries for {}".format(self.vmname))
                return False
            try:
                if self.headless_mode:
                    mode = "headless"
                else:
                    mode = "gui"
                print("Starting %s in %s mode" % (self.vmname, mode))
                self.progress = self.mach.launchVMProcess(self.session, mode, "")
                break
            except Exception as e:
                # This will usually happen if you try to start the same VM twice,
                # but may happen on loaded hosts too...
                log.warn("cannot launch VM {}, retrying {}: {}".format(self.vmname, retry + 1, e))
                time.sleep(0.6)
                continue

        try:
            self.progress.waitForCompletion(-1)
        except Exception:
            return False
        return True

    def _safeDisableNetAdpFromMachine(self, i_mach, i_vnic, disableAdapter=True):

        # _safe*() functions exist as a protection against COM failure on loaded hosts.
        log.debug("disabling network adapter for {}".format(self.vmname))
        # this command is retried several times, because it fails more often...
        retries = 6
        for retry in range(retries):
            if retry == (retries - 1):
                log.error("could not disable network adapter after 4 retries for {}".format(self.vmname))
                return False
            try:
                netadp = i_mach.getNetworkAdapter(i_vnic)
                netadp.traceEnabled = False
                netadp.attachmentType = self.constants.NetworkAttachmentType_Null
                if disableAdapter:
                    netadp.enabled = False
                break
            except Exception as e:
                # usually due to COM Error: "The object is not ready"
                log.warn("cannot disable network adapter for {}, retrying {}: {}".format(self.vmname, retry + 1, e))
                time.sleep(1)
                continue
        return True

    def _safeDetachNetAdp(self, i_netadp):

        #_safe*() functions exist as a protection against COM failure on loaded hosts.
        log.debug("detaching network adapter for {}".format(self.vmname))
        try:
            i_netadp.enabled = True
            i_netadp.attachmentType = self.constants.NetworkAttachmentType_Null
            i_netadp.cableConnected = False
        except Exception as e:
            # usually due to COM Error: "The object is not ready"
            log.error("cannot detach network adapter for {}: {}".format(self.vmname, e))
            return False
        return True

    def _safeSaveSettings(self, i_mach):

        # _safe*() functions exist as a protection against COM failure on loaded hosts.
        log.debug("saving setting for {}".format(self.vmname))
        try:
            i_mach.saveSettings()
        except Exception as e:
            # usually due to COM Error: "The object is not ready"
            log.error("cannot save settings for {}: {}".format(self.vmname, e))
            return False
        return True

    def _safeGetSessionObject(self):

        # _safe*() functions exist as a protection against COM failure on loaded hosts.
        log.debug("getting session for {}".format(self.vmname))
        try:
            self.session = self.mgr.mgr.getSessionObject(self.vbox)
        except Exception as e:
            # fails on heavily loaded hosts...
            log.error("cannot get session for {}: {}".format(self.vmname, e))
            return False
        return True

    def _safeNetOptions(self):

        # _safe*() functions exist as a protection against COM failure on loaded hosts.
        log.debug("getting network adapter options for {}".format(self.vmname))
        # this command is retried several times, because it fails more often...
        retries = 4
        for retry in range(retries):
            if retry == (retries - 1):
                log.error("could not get network adapter options after 4 retries for {}".format(self.vmname))
                return False
            if self._net_options():
                break
            else:
                # fails on heavily loaded hosts...
                log.warn("cannot get network adapter options for {}, retrying {}".format(self.vmname, retry + 1))
                time.sleep(1)
                continue
        return True

    def _safeConsoleOptions(self):

        #_safe*() functions exist as a protection against COM failure on loaded hosts.
        log.debug("getting console options for {}".format(self.vmname))
        # this command is retried several times, because it fails more often...
        retries = 4
        for retry in range(retries):
            if retry == (retries - 1):
                log.error("could not get console options after 4 retries for {}".format(self.vmname))
                return False
            if self._console_options():
                break
            else:
                # fails on heavily loaded hosts...
                log.warn("cannot get console options for {}, retrying {}".format(self.vmname, retry + 1))
                time.sleep(1)
                continue
        return True

    def _safeLockMachine(self):

        # _safe*() functions exist as a protection against COM failure on loaded hosts.
        log.debug("locking machine for {}".format(self.vmname))
        # this command is retried several times, because it fails more often...
        retries = 4
        for retry in range(retries):
            if retry == (retries - 1):
                log.error("could not get lock the machine after 4 retries for {}".format(self.vmname))
                return False
            try:
                self.mach.lockMachine(self.session, 1)
                break
            except Exception as e:
                log.warn("cannot lock the machine for {}, retrying {}: {}".format(self.vmname, retry + 1, e))
                time.sleep(1)
                continue
        return True

    def _safeUnlockMachine(self):

        # _safe*() functions exist as a protection against COM failure on loaded hosts.
        log.debug("unlocking machine for {}".format(self.vmname))
        # this command is retried several times, because it fails more often...
        retries = 4
        for retry in range(retries):
            if retry == (retries - 1):
                log.error("could not get unlock the machine after 4 retries for {}".format(self.vmname))
                return False
            try:
                self.session.unlockMachine()
                break
            except Exception as e:
                log.warn("cannot unlock the machine for {}, retrying {}: {}".format(self.vmname, retry + 1, e))
                time.sleep(1)
                continue
        return True
