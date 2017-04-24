"""
Copyright (c) 2012-2017 RockStor, Inc. <http://rockstor.com>
This file is part of RockStor.

RockStor is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published
by the Free Software Foundation; either version 2 of the License,
or (at your option) any later version.

RockStor is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.
"""
import os
import re
from tempfile import mkstemp
import shutil
from system.exceptions import CommandException
from system.osi import run_command, get_uuid_name_map
import logging

logger = logging.getLogger(__name__)

CRYPTSETUP = '/usr/sbin/cryptsetup'
DMSETUP = '/usr/sbin/dmsetup'
CRYPTTABFILE = '/etc/crypttab'
DD = '/usr/bin/dd'


def get_open_luks_container_dev(mapped_device_name, test=None):
    """
    Returns the parent device of an open LUKS container, ie if passed:
    luks-b8f89d97-f135-450f-9620-80a9fb421403
    (with or without a leading /dev/mapper/)
    it would return the following example device name string:
    /dev/sda3
    So /dev/sda3 is the LUKS container that once opened is mapped as our
    device_name in /dev/mapper
    :param mapped_device_name: any mapped device name accepted by cryptsetup,
    ie starting with "/dev/mapper/"
    :param test: if not None then it's contents is considered as substitute
    for the output of the cryptsetup command that is otherwise executed.
    :return: Empty string on any error or a device with path type /dev/vdd
    """
    container_dev = ''
    if test is None:
        out, err, rc = run_command([CRYPTSETUP, 'status', mapped_device_name],
                                   throw=False)
    else:
        # test mode so process test instead of cryptsetup output
        out = test
        rc = 0
    if rc != 0:  # if return code is an error return empty string
        return ''
    # search output of cryptsetup to find a line such as the following:
    #   device:  /dev/sda3
    for line in out:
        if line == '':
            continue
        # get line fields
        line_fields = line.split()
        # less than 2 fields are of no use so just in case:-
        if len(line_fields) < 2:
            continue
        if re.match('device:', line_fields[0]) is not None:
            # we have our line match so return it's second member
            return line_fields[1]
    return container_dev


def luks_format_disk(disk_byid, passphrase):
    """
    Formats disk_byid using supplied passphrase for master key encryption.
    Simple run_command wrapper to execute 'cryptsetup luksFormat <dev> path'
    Care is taken to immediately remove our temporary key-file (in ram) even 
    in the event of an Exception.
    :param disk_byid: by-id type name without path as found in db Disks.name.
    :param passphrase: luks passphrase used to encrypt master key.
    :return: o, e, rc tuple as returned by cryptsetup luksFormat command.
    """
    disk_byid_withpath = ('/dev/disk/by-id/%s' % disk_byid)
    # Create a temp file to pass our passphrase to our cryptsetup command.
    tfo, npath = mkstemp()
    # Pythons _candidate_tempdir_list() should ensure our npath temp file is
    # in memory (tmpfs). From https://docs.python.org/2/library/tempfile.html
    # we have "Creates a temporary file in the most secure manner possible."
    # Populate this file with our passphrase and use as cryptsetup keyfile.
    try:
        with open(npath, 'w') as passphrase_file_object:
            passphrase_file_object.write(passphrase)
        cmd = [CRYPTSETUP, 'luksFormat', disk_byid_withpath, npath]
        out, err, rc = run_command(cmd)
    except Exception as e:
        msg = ('Exception while running command(%s): %s' %
               (cmd, e.__str__()))
        raise Exception(msg)
    finally:
        passphrase_file_object.close()
        if os.path.exists(npath):
            try:
                os.remove(npath)
            except Exception as e:
                msg = ('Exception while removing temp file %s: %s' %
                       (npath, e.__str__()))
                raise Exception(msg)
    return out, err, rc


def get_unlocked_luks_containers_uuids():
    """
    Returns a list of LUKS container uuids backing open LUKS volumes. 
    The method used is to first run:
    'dmsetup info --columns --noheadings -o name --target crypt' eg output:
    luks-82fd9db1-e1c1-488d-9b42-536d0a82caeb
    luks-3efb3830-fee1-4a9e-a5c6-ea456bfc269e
    luks-a47f4950-3296-4504-b9a4-2dc75681a6ad
    to get a list of open LUKS containers (--target crypt). If the usual naming 
    convention is followed we have a name format of luks-<uuid> with len = 41
    and we can extract the uuid of the LUKS container from it syntactically.
    If this naming convention is not matched then we fail over to calling:
    get_open_luks_container_dev() and then looking up that devices uuid via
    our uuid_name_map dictionary.
    :return: list containing the uuids of LUKS containers that have currently
    open volumes, or empty list if none open or an error occurred. 
    """
    open_luks_container_uuids = []
    # flag to minimise calls to get_uuid_name_map()
    uuid_name_map_retrieved = False
    uuid_name_map = {}
    out, err, rc = run_command([DMSETUP, 'info', '--columns', '--noheadings',
                                '--options', 'name', '--target', 'crypt'])
    if len(out) > 0 and rc == 0:
        # The output has at least one line and our dmsetup executed OK.
        for each_line in out:
            if each_line == '':
                continue
            backing_container_uuid = None
            if len(each_line) == 41 and re.match('luks-', each_line):
                # good chance on "luks-a47f4950-3296-4504-b9a4-2dc75681a6ad"
                # naming convention so strip uuid from this (cheap and quick)
                backing_container_uuid = each_line[5:]
            else:
                # More expensive two step process to retrieve uuid of LUKS
                # container backing this open LUKS volume.
                # Initial call to gain backing device name for our container
                container_dev = get_open_luks_container_dev(each_line)
                # strip leading /dev/ from device name if any returned.
                if container_dev is not '':
                    container_dev = container_dev.split('/')[-1]
                    # should now have name without path ie 'vdd' ready to
                    # index our uuid_name_map.
                    if not uuid_name_map_retrieved:
                        uuid_name_map = get_uuid_name_map()
                        uuid_name_map_retrieved = True
                    # second stage where we look up this devices uuid
                    backing_container_uuid = uuid_name_map[container_dev]
            # if a backing container uuid was found add it to our list
            if backing_container_uuid is not None:
                open_luks_container_uuids.append(backing_container_uuid)
    return open_luks_container_uuids


def get_crypttab_entries():
    """
    Scans /etc/crypttab and parses into mapper name (/dev/mapper/) and uuid
    of device being mapped. The expected format of the file is:
    <mapper name(target dev)> UUID=<uuid>(source dev) none(or keyfile)
    There are other formats but this is modeled on the common format and that
    used by the anaconda installer when the "encrypt my data" tick is selected.
    A typical entry is as follows:
    luks-<uuid> UUID=<uuid> none
    N.B. a fourth column can be used to specify additional options ie "luks"
    but this column is redundant in the case of luks.  
    :return: dictionary indexed by the uuids of LUKS containers that have a 
    current crypttab entry where the value represents column 3, ie none for 
    password on boot, or the full path of a keyfile.
    """
    in_crypttab = {}
    if os.path.isfile(CRYPTTABFILE):
        with open(CRYPTTABFILE, "r") as ino:
            for line in ino.readlines():  # readlines reads whole file in one.
                if line == '\n' or re.match(line, '#'):
                    # empty line (a newline char) or begins with # so skip
                    continue
                line_fields = line.split()
                if len(line_fields) < 3:
                    # we expect at least 3 entries, ignore otherwise
                    continue
                if re.match('UUID=', line_fields[1]) is not None:
                    # we have a UUID= entry, perform basic validation
                    uuid_entry_fields = line_fields[1].split('=')
                    if len(uuid_entry_fields) == 2:
                        # we have at least 2 components: 'UUID', '<uuid>'
                        # split via '='
                        if len(uuid_entry_fields[1]) == 36:
                            # We have a 36 char long string, assuming uuid4
                            # stash the 3rd column entry in crypttab
                            in_crypttab[uuid_entry_fields[1]] = line_fields[2]
    return in_crypttab


def update_crypttab(uuid, keyfile_entry):
    """
    :param uuid: 
    :param keyfile_entry: 
    :return: False if crypttab edit failed or no uuid passed, True otherwise.
    otherwise.
    """
    # Deal elegantly with null or '' uuid
    if (uuid is None) or uuid == '':
        return False
    uuid_name_map_retrieved = False
    # Simpler paths for when no /etc/crypttab file exists.
    if not os.path.isfile(CRYPTTABFILE):
        if keyfile_entry == 'false':
            # The string 'false' is used to denote the removal of an existing
            # entry so we are essentially done as by whatever means there are
            # no entries in a non-existent crypttab.
            return True
        # We have no existing cryptab but a pending non 'false' entry.
        # Call specialized single entry crypttab creation method.
        return new_crypttab_single_entry(uuid, keyfile_entry)
    # By now we have an existing /etc/crypttab so we open it in readonly and
    # 'on the fly' edit line by line into a secure temp file.
    tfo, npath = mkstemp()
    # Pythons _candidate_tempdir_list() should ensure our npath temp file is
    # in memory (tmpfs). From https://docs.python.org/2/library/tempfile.html
    # we have "Creates a temporary file in the most secure manner possible."
    with open(CRYPTTABFILE, 'r') as ct_original, open(npath, 'w') as temp_file:
        # examine original crypttab line by line.
        new_entry = None # temp var that doubles as flag for entry made.
        for line in ct_original.readlines():  # readlines (whole file in one).
            update_line = False
            if line == '\n' or re.match(line, '#') is not None:
                # blank line (return) or remark line, strip for simplicity.
                continue
            line_fields = line.split()
            # sanitize remaining lines, bare minimum count of entries eg:
            # mapper-name source-dev
            # however 3 is a more modern minimum so drop < 3 column entries.
            if len(line_fields) < 3:
                continue
            # We have a viable line of at least 3 columns so entertain it.
            # Interpret the source device entry in second column (index 1)
            if re.match('UUID=', line_fields[1]) is not None:
                # we have our native UUID reference so split and compare
                source_dev_fields = line_fields[1].split('=')
                if len(source_dev_fields) is not 2:
                    # ie "UUID=" with no value which is non legit so skip
                    continue
                # we should have a UUID=<something> entry so examine it
                if source_dev_fields[1] == uuid:
                    # Matching source device uuid entry so set flag
                    update_line = True
                else:
                    # no UUID= type entry found so check for dev name
                    # eg instead of 'UUID=<uuid>' we have eg: '/dev/sdd'
                    if re.match('/dev', source_dev_fields[1]) is not None:
                        # We have a dev entry so strip the path.
                        dev_no_path = source_dev_fields[1].split('/')[-1]
                        # index our uuid_name_map for dev name comparison
                        if not uuid_name_map_retrieved:
                            uuid_name_map = get_uuid_name_map()
                            uuid_name_map_retrieved = True
                        uuid_of_source = uuid_name_map[dev_no_path]
                        if uuid_of_source == uuid:
                            # we have a non native /dev type entry but
                            # the uuid's match so replace with quicker
                            # native form of luks-<uuid> UUID=<uuid> etc
                            update_line = True
            if update_line:
                # We have a device match by uuid with an existing line.
                if keyfile_entry == 'false':
                    # The string 'false' is used to denote no crypttab entry,
                    # this we can do by simply skipping this line.
                    continue
                # Update the line with our native format but try and
                # preserve custom options in column 4 if they exist:
                # Use new mapper name (potentially controversial).
                if len(line_fields) > 3:
                    new_entry = ('luks-%s UUID=%s %s %s\n' %
                                 (uuid, uuid, keyfile_entry,
                                  ' '.join(line_fields[3:])))
                else:
                    # we must have a 3 column entry (>= 3 and then > 3)
                    # N.B. later 'man crypttab' suggests 4 columns as
                    # mandatory but that was not observed. We add 'luks'
                    # as fourth column entry just in case.
                    new_entry = ('luks-%s UUID=%s %s luks\n' % (uuid, uuid,
                                                                keyfile_entry))
                temp_file.write(new_entry)
            else:
                # No update flag and no original line skip so we
                # simply copy over what ever line we found. Most likely a non
                # matching device.
                temp_file.write(line)
        if keyfile_entry != 'false' and new_entry is None:
            # We have scanned the existing crypttab and not yet made our edit.
            # The string 'false' is used to denote no crypttab entry and if
            # new_entry is still None we have made no edit.
            new_entry = ('luks-%s UUID=%s %s luks\n' % (uuid, uuid,
                                                        keyfile_entry))
            temp_file.write(new_entry)
    # secure temp file now holds our proposed (post edit) crypttab.
    # Copy contents over existing crypttab and ensure tempfile is removed.
    try:
        # shutil.copy2 is equivalent to cp -p (preserver attributes).
        # This preserves the secure defaults of the temp file without having
        # to chmod there after. Result is the desired:
        # -rw------- 1 root root
        # ie rw to root only or 0600
        # and avoiding a window prior to a separate chmod command.
        shutil.copy2(npath, CRYPTTABFILE)
    except Exception as e:
        msg = ('Exception while creating fresh %s: %s' % (CRYPTTABFILE,
                                                          e.__str__()))
        raise Exception(msg)
    finally:
        if os.path.exists(npath):
            try:
                os.remove(npath)
            except Exception as e:
                msg = ('Exception while removing temp file %s: %s' %
                       (npath, e.__str__()))
                raise Exception(msg)
    return True


def new_crypttab_single_entry(uuid, keyfile_entry):
    # Create a temp file to construct our /etc/crypttab in prior to copying
    # with preserved attributes.
    tfo, npath = mkstemp()
    # Pythons _candidate_tempdir_list() should ensure our npath temp file is
    # in memory (tmpfs). From https://docs.python.org/2/library/tempfile.html
    # we have "Creates a temporary file in the most secure manner possible."
    crypttab_line = ('luks-%s UUID=%s %s luks\n' % (uuid, uuid, keyfile_entry))
    try:
        with open(npath, "w") as tempfo:
            tempfo.write(crypttab_line)
        # shutil.copy2 is equivalent to cp -p (preserver attributes).
        # This preserves the secure defaults of the temp file without having
        # to chmod there after. Result is the desired:
        # -rw------- 1 root root
        # ie rw to root only or 0600
        # and avoiding a window prior to a separate chmod command.
        shutil.copy2(npath, CRYPTTABFILE)
    except Exception as e:
        msg = ('Exception while creating fresh %s: %s' % (CRYPTTABFILE,
                                                          e.__str__()))
        raise Exception(msg)
    finally:
        if os.path.exists(npath):
            try:
                os.remove(npath)
            except Exception as e:
                msg = ('Exception while removing temp file %s: %s' %
                       (npath, e.__str__()))
                raise Exception(msg)
    return True


def add_keyfile(dev_byid, keyfile_withpath, passphrase):
    """
    Ensures that the given keyfile_withpath exists and calls create_keyfile()
    if it doesn't. Then attempts to register the established keyfile with the
    dev_byid device via "cryptsetup luksAddKey dev keyfile passphrase.
    N.B. The passphrase is passed to the command via a secure temporary file.
    Care is taken to remove this file irrespective of outcome.
    :param dev_byid: by-id type name without path as found in db Disks.name.
    :param keyfile_withpath: the intended keyfile with full path.
    :param passphrase: LUKS passphrase: any current key slot passphrase.
    :return: True if keyfile successfully registered. False or an Exception 
    is raised in all other instances.
    """
    # First we establish if our keyfile exists, and if not we create it.
    if not os.path.isfile(keyfile_withpath):
        # attempt to create our keyfile:
        if not create_keyfile(keyfile_withpath):
            # msg = ('Failed to establish new or existing keyfile: %s: %s' %
            #        (keyfile_withpath, e.__str__()))
            # raise Exception(msg)
            return False
    # We are by now assured of an existing keyfile so register this with our
    # LUKS container:
    dev_byid_withpath = '/dev/disk/by-id/%s' % dev_byid
    tfo, npath = mkstemp()
    # # Pythons _candidate_tempdir_list() should ensure our npath temp file is
    # # in memory (tmpfs). From https://docs.python.org/2/library/tempfile.html
    # # we have "Creates a temporary file in the most secure manner possible."
    # # Populate this file with our passphrase and use as cryptsetup keyfile.
    rc = 0
    try:
        with open(npath, 'w') as passphrase_file_object:
            passphrase_file_object.write(passphrase)
        cmd = [CRYPTSETUP, 'luksAddKey', dev_byid_withpath, keyfile_withpath,
               '--key-file', npath]
        out, err, rc = run_command(cmd, throw=False)
        if rc != 0:
            raise CommandException(('%s' % cmd), out, err, rc)
    except Exception as e:
        if rc == 1:
            msg = 'Wrong Parameters exception'
        elif rc == 2:
            msg = 'No Permission (Bad Passphrase) exception'
        elif rc == 3:
            msg = 'Out of Memory exception'
        elif rc == 4:
            msg = 'Wrong Device Specified'
        elif rc == 5:
            msg = "Device already exists or device is busy"
        else:
            msg = 'Exception'
        msg += ' while running command(%s): %s' % (cmd, e.__str__())
        raise Exception(msg)
    finally:
        passphrase_file_object.close()
        if os.path.exists(npath):
            try:
                os.remove(npath)
            except Exception as e:
                msg = ('Exception while removing temp file %s: %s' %
                       (npath, e.__str__()))
                raise Exception(msg)
    return True


def create_keyfile(keyfile_withpath):
    """
    Function to create a random keyfile appropriate for LUKS use. Works by 
    initially creating a temp file with the appropriate context and then
    copying the contents over. This minimises lock time on our target keyfile. 
    Currently hardwired to make 2048 byte /dev/urandom sourced keyfiles.
    This is equivalent to a 2^14bit keyfile.
    :param keyfile_withpath: 
    :return: True on success, or if the keyfile_with_path exists, False
    otherwise.  
    """
    # If our target file exists we are done and return True (no overwriting).
    if os.path.isfile(keyfile_withpath):
        return True
    # Otherwise we generate the keyfile.
    tfo, npath = mkstemp()
    # Pythons _candidate_tempdir_list() should ensure our npath temp file is
    # in memory (tmpfs). From https://docs.python.org/2/library/tempfile.html
    # we have "Creates a temporary file in the most secure manner possible."
    try:
        with open(npath, 'w') as temp_keyfile:
            cmd = [DD, 'bs=512', 'count=4', 'if=/dev/urandom', 'of=%s' % npath]
            out, err, rc = run_command(cmd, throw=False)
        if rc != 0:
            logger.debug('create_keyfile failed cmd=%s' % cmd)
            return False
        # shutil.copy2 is equivalent to cp -p (preserver attributes).
        # This preserves the secure defaults of the temp file without having
        # to chmod there after. Result is the desired:
        # -rw------- 1 root root
        # ie rw to root only or 0600
        # and avoiding a window prior to a separate chmod command.
        shutil.copy2(npath, keyfile_withpath)
    except Exception as e:
        msg = ('Exception while creating keyfile %s: %s' % (keyfile_withpath,
                                                            e.__str__()))
        raise Exception(msg)
    finally:
        # make sure we remove our temp file (just in case it became a keyfile)
        temp_keyfile.close()
        if os.path.isfile(npath):
            try:
                os.remove(npath)
            except Exception as e:
                msg = ('Exception while removing temp file %s: %s' %
                       (npath, e.__str__()))
                raise Exception(msg)
    return True


def native_keyfile_exists(uuid):
    """
    Simple wrapper around os.path.isfile(/root/keyfile-<uuid>) to establish if
    a Rockstor native keyfile exists. 
    :return: True if /root/keyfile-<uuid> exists, False otherwise.
    """
    try:
        return os.path.isfile('/root/keyfile-%s' % uuid)
    except:
        return False
