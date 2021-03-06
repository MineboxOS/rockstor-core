"""
Copyright (c) 2012-2014 RockStor, Inc. <http://rockstor.com>
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

import time
from storageadmin.exceptions import RockStorAPIException
from storageadmin.models import Appliance
from cli import APIWrapper
import logging
logger = logging.getLogger(__name__)


class ReplicationMixin(object):

    def validate_src_share(self, sender_uuid, sname):
        url = 'https://'
        if (self.raw is None):
            a = Appliance.objects.get(uuid=sender_uuid)
            url = ('%s%s:%s' % (url, a.ip, a.mgmt_port))
            self.raw = APIWrapper(client_id=a.client_id,
                                  client_secret=a.client_secret,
                                  url=url)
        return self.raw.api_call(url='shares/%s' % sname)

    def update_replica_status(self, rtid, data):
        try:
            url = ('sm/replicas/trail/%d' % rtid)
            return self.law.api_call(url, data=data, calltype='put')
        except Exception as e:
            msg = ('Exception while updating replica(%s) status to %s: %s' %
                   (url, data['status'], e.__str__()))
            raise Exception(msg)

    def disable_replica(self, rid):
        try:
            url = ('sm/replicas/%d' % rid)
            headers = {'content-type': 'application/json', }
            return self.law.api_call(url, data={'enabled': False, },
                                     calltype='put', save_error=False,
                                     headers=headers)
        except Exception as e:
            msg = ('Exception while disabling replica(%s): %s' %
                   (url, e.__str__()))
            raise Exception(msg)

    def create_replica_trail(self, rid, snap_name):
        url = ('sm/replicas/trail/replica/%d' % rid)
        return self.law.api_call(url, data={'snap_name': snap_name, },
                                 calltype='post', save_error=False)

    def rshare_id(self, sname):
        url = ('sm/replicas/rshare/%s' % sname)
        rshare = self.law.api_call(url, save_error=False)
        return rshare['id']

    def create_rshare(self, data):
        try:
            url = 'sm/replicas/rshare'
            rshare = self.law.api_call(url, data=data, calltype='post',
                                       save_error=False)
            return rshare['id']
        except RockStorAPIException as e:
            if (e.detail == 'Replicashare(%s) already exists.' % data['share']):  # noqa E501
                return self.rshare_id(data['share'])
            raise e

    def create_receive_trail(self, rid, data):
        url = ('sm/replicas/rtrail/rshare/%d' % rid)
        rt = self.law.api_call(url, data=data, calltype='post',
                               save_error=False)
        return rt['id']

    def update_receive_trail(self, rtid, data):
        url = ('sm/replicas/rtrail/%d' % rtid)
        try:
            return self.law.api_call(url, data=data, calltype='put',
                                     save_error=False)
        except Exception as e:
            msg = ('Exception while updating receive trail(%s): %s' %
                   (url, e.__str__()))
            raise Exception(msg)

    def prune_trail(self, url, days=7):
        try:
            data = {'days': days, }
            return self.law.api_call(url, data=data, calltype='delete',
                                     save_error=False)
        except Exception as e:
            msg = ('Exception while pruning trail for url(%s): %s'
                   % (url, e.__str__()))
            raise Exception(msg)

    def prune_receive_trail(self, ro):
        url = ('sm/replicas/rtrail/rshare/%d' % ro.id)
        return self.prune_trail(url)

    def prune_replica_trail(self, ro):
        url = ('sm/replicas/trail/replica/%d' % ro.id)
        return self.prune_trail(url)

    def create_snapshot(self, sname, snap_name, snap_type='replication'):
        try:
            url = ('shares/%s/snapshots/%s' % (sname, snap_name))
            return self.law.api_call(url, data={'snap_type': snap_type, },
                                     calltype='post', save_error=False)
        except RockStorAPIException as e:
            if (e.detail == ('Snapshot(%s) already exists for the Share(%s).' %
                             (snap_name, sname))):
                return logger.debug(e.detail)
            raise e

    def delete_snapshot(self, sname, snap_name):
        try:
            url = ('shares/%s/snapshots/%s' % (sname, snap_name))
            self.law.api_call(url, calltype='delete', save_error=False)
            return True
        except RockStorAPIException as e:
            if (e.detail == 'Snapshot(%s) does not exist.' % snap_name):
                logger.debug(e.detail)
                return False
            raise e

    def create_share(self, sname, pool):
        try:
            url = 'shares'
            data = {'pool': pool,
                    'replica': True,
                    'sname': sname, }
            headers = {'content-type': 'application/json', }
            return self.law.api_call(url, data=data, calltype='post',
                                     headers=headers, save_error=False)
        except RockStorAPIException as e:
            if (e.detail == 'Share(%s) already exists. Choose a different name' % sname):  # noqa E501
                return logger.debug(e.detail)
            raise e

    def refresh_snapshot_state(self):
        try:
            return self.law.api_call('commands/refresh-snapshot-state',
                                     data=None, calltype='post',
                                     save_error=False)
        except Exception as e:
            logger.error('Exception while refreshing Snapshot state: %s'
                         % e.__str__())

    def refresh_share_state(self):
        try:
            return self.law.api_call('commands/refresh-share-state', data=None,
                                     calltype='post', save_error=False)
        except Exception as e:
            logger.error('Exception while refresh Shar state: %s'
                         % e.__str__())

    def humanize_bytes(self, num, units=('Bytes', 'KB', 'MB', 'GB',)):
        if (num < 1024 or len(units) == 1):
            return '%.2f %s' % (num, units[0])
        return self.humanize_bytes(num/1024, units[1:])

    def size_report(self, num, t0):
        t1 = time.time()
        dsize = self.humanize_bytes(float(num))
        drate = self.humanize_bytes(float(num/(t1 - t0)))
        return dsize, drate
