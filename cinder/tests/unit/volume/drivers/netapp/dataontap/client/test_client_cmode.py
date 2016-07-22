# Copyright (c) 2014 Alex Meade.  All rights reserved.
# Copyright (c) 2015 Dustin Schoenbrun. All rights reserved.
# Copyright (c) 2015 Tom Barron.  All rights reserved.
# Copyright (c) 2016 Mike Rooney. All rights reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import copy
import uuid

import ddt
from lxml import etree
import mock
import paramiko
import six

from cinder import exception
from cinder import ssh_utils
from cinder import test
from cinder.tests.unit.volume.drivers.netapp.dataontap.client import (
    fakes as fake_client)
from cinder.tests.unit.volume.drivers.netapp.dataontap import fakes as fake
from cinder.volume.drivers.netapp.dataontap.client import api as netapp_api
from cinder.volume.drivers.netapp.dataontap.client import client_cmode
from cinder.volume.drivers.netapp import utils as netapp_utils


CONNECTION_INFO = {'hostname': 'hostname',
                   'transport_type': 'https',
                   'port': 443,
                   'username': 'admin',
                   'password': 'passw0rd',
                   'vserver': 'fake_vserver'}


@ddt.ddt
class NetAppCmodeClientTestCase(test.TestCase):

    def setUp(self):
        super(NetAppCmodeClientTestCase, self).setUp()

        self.mock_object(client_cmode.Client, '_init_ssh_client')
        with mock.patch.object(client_cmode.Client,
                               'get_ontapi_version',
                               return_value=(1, 20)):
            self.client = client_cmode.Client(**CONNECTION_INFO)

        self.client.ssh_client = mock.MagicMock()
        self.client.connection = mock.MagicMock()
        self.connection = self.client.connection

        self.vserver = CONNECTION_INFO['vserver']
        self.fake_volume = six.text_type(uuid.uuid4())
        self.fake_lun = six.text_type(uuid.uuid4())
        self.mock_send_request = self.mock_object(self.client, 'send_request')

    def tearDown(self):
        super(NetAppCmodeClientTestCase, self).tearDown()

    def _mock_api_error(self, code='fake'):
        return mock.Mock(side_effect=netapp_api.NaApiError(code=code))

    def test_has_records(self):

        result = self.client._has_records(netapp_api.NaElement(
            fake_client.QOS_POLICY_GROUP_GET_ITER_RESPONSE))

        self.assertTrue(result)

    def test_has_records_not_found(self):

        result = self.client._has_records(
            netapp_api.NaElement(fake_client.NO_RECORDS_RESPONSE))

        self.assertFalse(result)

    @ddt.data((fake_client.AGGR_GET_ITER_RESPONSE, 2),
              (fake_client.NO_RECORDS_RESPONSE, 0))
    @ddt.unpack
    def test_get_record_count(self, response, expected):

        api_response = netapp_api.NaElement(response)

        result = self.client._get_record_count(api_response)

        self.assertEqual(expected, result)

    def test_get_records_count_invalid(self):

        api_response = netapp_api.NaElement(
            fake_client.INVALID_GET_ITER_RESPONSE_NO_RECORDS)

        self.assertRaises(exception.NetAppDriverException,
                          self.client._get_record_count,
                          api_response)

    @ddt.data(True, False)
    def test_send_iter_request(self, enable_tunneling):

        api_responses = [
            netapp_api.NaElement(
                fake_client.STORAGE_DISK_GET_ITER_RESPONSE_PAGE_1),
            netapp_api.NaElement(
                fake_client.STORAGE_DISK_GET_ITER_RESPONSE_PAGE_2),
            netapp_api.NaElement(
                fake_client.STORAGE_DISK_GET_ITER_RESPONSE_PAGE_3),
        ]
        mock_send_request = self.mock_object(
            self.client, 'send_request',
            mock.Mock(side_effect=copy.deepcopy(api_responses)))

        storage_disk_get_iter_args = {
            'desired-attributes': {
                'storage-disk-info': {
                    'disk-name': None,
                }
            }
        }
        result = self.client.send_iter_request(
            'storage-disk-get-iter', api_args=storage_disk_get_iter_args,
            enable_tunneling=enable_tunneling, max_page_length=10)

        num_records = result.get_child_content('num-records')
        self.assertEqual('28', num_records)
        next_tag = result.get_child_content('next-tag')
        self.assertEqual('', next_tag)

        args1 = copy.deepcopy(storage_disk_get_iter_args)
        args1['max-records'] = 10
        args2 = copy.deepcopy(storage_disk_get_iter_args)
        args2['max-records'] = 10
        args2['tag'] = 'next_tag_1'
        args3 = copy.deepcopy(storage_disk_get_iter_args)
        args3['max-records'] = 10
        args3['tag'] = 'next_tag_2'

        mock_send_request.assert_has_calls([
            mock.call('storage-disk-get-iter', args1,
                      enable_tunneling=enable_tunneling),
            mock.call('storage-disk-get-iter', args2,
                      enable_tunneling=enable_tunneling),
            mock.call('storage-disk-get-iter', args3,
                      enable_tunneling=enable_tunneling),
        ])

    def test_send_iter_request_single_page(self):

        api_response = netapp_api.NaElement(
            fake_client.STORAGE_DISK_GET_ITER_RESPONSE)
        mock_send_request = self.mock_object(
            self.client, 'send_request',
            mock.Mock(return_value=api_response))

        storage_disk_get_iter_args = {
            'desired-attributes': {
                'storage-disk-info': {
                    'disk-name': None,
                }
            }
        }
        result = self.client.send_iter_request(
            'storage-disk-get-iter', api_args=storage_disk_get_iter_args,
            max_page_length=10)

        num_records = result.get_child_content('num-records')
        self.assertEqual('1', num_records)

        args = copy.deepcopy(storage_disk_get_iter_args)
        args['max-records'] = 10

        mock_send_request.assert_has_calls([
            mock.call('storage-disk-get-iter', args, enable_tunneling=True),
        ])

    def test_send_iter_request_not_found(self):

        api_response = netapp_api.NaElement(fake_client.NO_RECORDS_RESPONSE)
        mock_send_request = self.mock_object(
            self.client, 'send_request',
            mock.Mock(return_value=api_response))

        result = self.client.send_iter_request('storage-disk-get-iter')

        num_records = result.get_child_content('num-records')
        self.assertEqual('0', num_records)

        args = {'max-records': client_cmode.DEFAULT_MAX_PAGE_LENGTH}

        mock_send_request.assert_has_calls([
            mock.call('storage-disk-get-iter', args, enable_tunneling=True),
        ])

    @ddt.data(fake_client.INVALID_GET_ITER_RESPONSE_NO_ATTRIBUTES,
              fake_client.INVALID_GET_ITER_RESPONSE_NO_RECORDS)
    def test_send_iter_request_invalid(self, fake_response):

        api_response = netapp_api.NaElement(fake_response)
        self.mock_object(self.client,
                         'send_request',
                         mock.Mock(return_value=api_response))

        self.assertRaises(exception.NetAppDriverException,
                          self.client.send_iter_request,
                          'storage-disk-get-iter')

    def test_get_iscsi_target_details_no_targets(self):
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>1</num-records>
                            <attributes-list></attributes-list>
                          </results>"""))
        self.connection.invoke_successfully.return_value = response
        target_list = self.client.get_iscsi_target_details()

        self.assertEqual([], target_list)

    def test_get_iscsi_target_details(self):
        expected_target = {
            "address": "127.0.0.1",
            "port": "1337",
            "interface-enabled": "true",
            "tpgroup-tag": "7777",
        }
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>1</num-records>
                            <attributes-list>
                              <iscsi-interface-list-entry-info>
                                <ip-address>%(address)s</ip-address>
                                <ip-port>%(port)s</ip-port>
            <is-interface-enabled>%(interface-enabled)s</is-interface-enabled>
                                <tpgroup-tag>%(tpgroup-tag)s</tpgroup-tag>
                              </iscsi-interface-list-entry-info>
                            </attributes-list>
                          </results>""" % expected_target))
        self.connection.invoke_successfully.return_value = response

        target_list = self.client.get_iscsi_target_details()

        self.assertEqual([expected_target], target_list)

    def test_get_iscsi_service_details_with_no_iscsi_service(self):
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>0</num-records>
                          </results>"""))
        self.connection.invoke_successfully.return_value = response

        iqn = self.client.get_iscsi_service_details()

        self.assertIsNone(iqn)

    def test_get_iscsi_service_details(self):
        expected_iqn = 'iqn.1998-01.org.openstack.iscsi:name1'
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>1</num-records>
                            <attributes-list>
                              <iscsi-service-info>
                                <node-name>%s</node-name>
                              </iscsi-service-info>
                            </attributes-list>
                          </results>""" % expected_iqn))
        self.connection.invoke_successfully.return_value = response

        iqn = self.client.get_iscsi_service_details()

        self.assertEqual(expected_iqn, iqn)

    def test_get_lun_list(self):
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>2</num-records>
                            <attributes-list>
                              <lun-info>
                              </lun-info>
                              <lun-info>
                              </lun-info>
                            </attributes-list>
                          </results>"""))
        self.connection.invoke_successfully.return_value = response

        luns = self.client.get_lun_list()

        self.assertEqual(2, len(luns))

    def test_get_lun_list_with_multiple_pages(self):
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>2</num-records>
                            <attributes-list>
                              <lun-info> </lun-info>
                              <lun-info> </lun-info>
                            </attributes-list>
                            <next-tag>fake-next</next-tag>
                          </results>"""))
        response_2 = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>2</num-records>
                            <attributes-list>
                              <lun-info> </lun-info>
                              <lun-info> </lun-info>
                            </attributes-list>
                          </results>"""))
        self.connection.invoke_successfully.side_effect = [response,
                                                           response_2]

        luns = self.client.get_lun_list()

        self.assertEqual(4, len(luns))

    def test_get_lun_map_no_luns_mapped(self):
        path = '/vol/%s/%s' % (self.fake_volume, self.fake_lun)
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>0</num-records>
                            <attributes-list></attributes-list>
                          </results>"""))
        self.connection.invoke_successfully.return_value = response

        lun_map = self.client.get_lun_map(path)

        self.assertEqual([], lun_map)

    def test_get_lun_map(self):
        path = '/vol/%s/%s' % (self.fake_volume, self.fake_lun)
        expected_lun_map = {
            "initiator-group": "igroup",
            "lun-id": "1337",
            "vserver": "vserver",
        }
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>1</num-records>
                            <attributes-list>
                              <lun-map-info>
                                <lun-id>%(lun-id)s</lun-id>
                        <initiator-group>%(initiator-group)s</initiator-group>
                                <vserver>%(vserver)s</vserver>
                              </lun-map-info>
                            </attributes-list>
                          </results>""" % expected_lun_map))
        self.connection.invoke_successfully.return_value = response

        lun_map = self.client.get_lun_map(path)

        self.assertEqual([expected_lun_map], lun_map)

    def test_get_lun_map_multiple_pages(self):
        path = '/vol/%s/%s' % (self.fake_volume, self.fake_lun)
        expected_lun_map = {
            "initiator-group": "igroup",
            "lun-id": "1337",
            "vserver": "vserver",
        }
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>1</num-records>
                            <attributes-list>
                              <lun-map-info>
                                <lun-id>%(lun-id)s</lun-id>
                        <initiator-group>%(initiator-group)s</initiator-group>
                                <vserver>%(vserver)s</vserver>
                              </lun-map-info>
                            </attributes-list>
                            <next-tag>blah</next-tag>
                          </results>""" % expected_lun_map))
        response_2 = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>1</num-records>
                            <attributes-list>
                              <lun-map-info>
                                <lun-id>%(lun-id)s</lun-id>
                        <initiator-group>%(initiator-group)s</initiator-group>
                                <vserver>%(vserver)s</vserver>
                              </lun-map-info>
                            </attributes-list>
                          </results>""" % expected_lun_map))
        self.connection.invoke_successfully.side_effect = [response,
                                                           response_2]

        lun_map = self.client.get_lun_map(path)

        self.assertEqual([expected_lun_map, expected_lun_map], lun_map)

    def test_get_igroup_by_initiator_none_found(self):
        initiator = 'initiator'
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>0</num-records>
                            <attributes-list></attributes-list>
                          </results>"""))
        self.connection.invoke_successfully.return_value = response

        igroup = self.client.get_igroup_by_initiators([initiator])

        self.assertEqual([], igroup)

    def test_get_igroup_by_initiators(self):
        initiators = ['11:22:33:44:55:66:77:88']
        expected_igroup = {
            'initiator-group-os-type': 'default',
            'initiator-group-type': 'fcp',
            'initiator-group-name': 'openstack-igroup1',
        }

        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
    <attributes-list>
      <initiator-group-info>
        <initiator-group-alua-enabled>true</initiator-group-alua-enabled>
        <initiator-group-name>%(initiator-group-name)s</initiator-group-name>
        <initiator-group-os-type>default</initiator-group-os-type>
        <initiator-group-throttle-borrow>false</initiator-group-throttle-borrow>
        <initiator-group-throttle-reserve>0</initiator-group-throttle-reserve>
        <initiator-group-type>%(initiator-group-type)s</initiator-group-type>
        <initiator-group-use-partner>true</initiator-group-use-partner>
        <initiator-group-uuid>f8aa707a-57fa-11e4-ad08-123478563412
        </initiator-group-uuid>
        <initiator-group-vsa-enabled>false</initiator-group-vsa-enabled>
        <initiators>
          <initiator-info>
            <initiator-name>11:22:33:44:55:66:77:88</initiator-name>
          </initiator-info>
        </initiators>
        <vserver>cinder-iscsi</vserver>
      </initiator-group-info>
    </attributes-list>
    <num-records>1</num-records>
  </results>""" % expected_igroup))
        self.connection.invoke_successfully.return_value = response

        igroups = self.client.get_igroup_by_initiators(initiators)

        # make these lists of dicts comparable using hashable dictionaries
        igroups = set(
            [netapp_utils.hashabledict(igroup) for igroup in igroups])
        expected = set([netapp_utils.hashabledict(expected_igroup)])

        self.assertSetEqual(igroups, expected)

    def test_get_igroup_by_initiators_multiple(self):
        initiators = ['11:22:33:44:55:66:77:88', '88:77:66:55:44:33:22:11']
        expected_igroup = {
            'initiator-group-os-type': 'default',
            'initiator-group-type': 'fcp',
            'initiator-group-name': 'openstack-igroup1',
        }

        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
    <attributes-list>
      <initiator-group-info>
        <initiator-group-alua-enabled>true</initiator-group-alua-enabled>
        <initiator-group-name>%(initiator-group-name)s</initiator-group-name>
        <initiator-group-os-type>default</initiator-group-os-type>
        <initiator-group-throttle-borrow>false</initiator-group-throttle-borrow>
        <initiator-group-throttle-reserve>0</initiator-group-throttle-reserve>
        <initiator-group-type>%(initiator-group-type)s</initiator-group-type>
        <initiator-group-use-partner>true</initiator-group-use-partner>
        <initiator-group-uuid>f8aa707a-57fa-11e4-ad08-123478563412
        </initiator-group-uuid>
        <initiator-group-vsa-enabled>false</initiator-group-vsa-enabled>
        <initiators>
          <initiator-info>
            <initiator-name>11:22:33:44:55:66:77:88</initiator-name>
          </initiator-info>
          <initiator-info>
            <initiator-name>88:77:66:55:44:33:22:11</initiator-name>
          </initiator-info>
        </initiators>
        <vserver>cinder-iscsi</vserver>
      </initiator-group-info>
    </attributes-list>
    <num-records>1</num-records>
  </results>""" % expected_igroup))
        self.connection.invoke_successfully.return_value = response

        igroups = self.client.get_igroup_by_initiators(initiators)

        # make these lists of dicts comparable using hashable dictionaries
        igroups = set(
            [netapp_utils.hashabledict(igroup) for igroup in igroups])
        expected = set([netapp_utils.hashabledict(expected_igroup)])

        self.assertSetEqual(igroups, expected)

    def test_get_igroup_by_initiators_multiple_pages(self):
        initiator = '11:22:33:44:55:66:77:88'
        expected_igroup1 = {
            'initiator-group-os-type': 'default',
            'initiator-group-type': 'fcp',
            'initiator-group-name': 'openstack-igroup1',
        }
        expected_igroup2 = {
            'initiator-group-os-type': 'default',
            'initiator-group-type': 'fcp',
            'initiator-group-name': 'openstack-igroup2',
        }
        response_1 = netapp_api.NaElement(
            etree.XML("""<results status="passed">
    <attributes-list>
      <initiator-group-info>
        <initiator-group-alua-enabled>true</initiator-group-alua-enabled>
        <initiator-group-name>%(initiator-group-name)s</initiator-group-name>
        <initiator-group-os-type>default</initiator-group-os-type>
        <initiator-group-throttle-borrow>false</initiator-group-throttle-borrow>
        <initiator-group-throttle-reserve>0</initiator-group-throttle-reserve>
        <initiator-group-type>%(initiator-group-type)s</initiator-group-type>
        <initiator-group-use-partner>true</initiator-group-use-partner>
        <initiator-group-uuid>f8aa707a-57fa-11e4-ad08-123478563412
        </initiator-group-uuid>
        <initiator-group-vsa-enabled>false</initiator-group-vsa-enabled>
        <initiators>
          <initiator-info>
            <initiator-name>11:22:33:44:55:66:77:88</initiator-name>
          </initiator-info>
        </initiators>
        <vserver>cinder-iscsi</vserver>
      </initiator-group-info>
    </attributes-list>
    <next-tag>12345</next-tag>
    <num-records>1</num-records>
  </results>""" % expected_igroup1))
        response_2 = netapp_api.NaElement(
            etree.XML("""<results status="passed">
    <attributes-list>
      <initiator-group-info>
        <initiator-group-alua-enabled>true</initiator-group-alua-enabled>
        <initiator-group-name>%(initiator-group-name)s</initiator-group-name>
        <initiator-group-os-type>default</initiator-group-os-type>
        <initiator-group-throttle-borrow>false</initiator-group-throttle-borrow>
        <initiator-group-throttle-reserve>0</initiator-group-throttle-reserve>
        <initiator-group-type>%(initiator-group-type)s</initiator-group-type>
        <initiator-group-use-partner>true</initiator-group-use-partner>
        <initiator-group-uuid>f8aa707a-57fa-11e4-ad08-123478563412
        </initiator-group-uuid>
        <initiator-group-vsa-enabled>false</initiator-group-vsa-enabled>
        <initiators>
          <initiator-info>
            <initiator-name>11:22:33:44:55:66:77:88</initiator-name>
          </initiator-info>
        </initiators>
        <vserver>cinder-iscsi</vserver>
      </initiator-group-info>
    </attributes-list>
    <num-records>1</num-records>
  </results>""" % expected_igroup2))
        self.connection.invoke_successfully.side_effect = [response_1,
                                                           response_2]

        igroups = self.client.get_igroup_by_initiators([initiator])

        # make these lists of dicts comparable using hashable dictionaries
        igroups = set(
            [netapp_utils.hashabledict(igroup) for igroup in igroups])
        expected = set([netapp_utils.hashabledict(expected_igroup1),
                        netapp_utils.hashabledict(expected_igroup2)])

        self.assertSetEqual(igroups, expected)

    def test_clone_lun(self):
        self.client.clone_lun(
            'volume', 'fakeLUN', 'newFakeLUN',
            qos_policy_group_name=fake.QOS_POLICY_GROUP_NAME)

        self.assertEqual(1, self.connection.invoke_successfully.call_count)

    @ddt.data({'supports_is_backup': True, 'is_snapshot': True},
              {'supports_is_backup': True, 'is_snapshot': False},
              {'supports_is_backup': False, 'is_snapshot': True},
              {'supports_is_backup': False, 'is_snapshot': False})
    @ddt.unpack
    def test_clone_lun_is_snapshot(self, supports_is_backup, is_snapshot):

        self.client.features.add_feature('BACKUP_CLONE_PARAM',
                                         supported=supports_is_backup)

        self.client.clone_lun(
            'volume', 'fakeLUN', 'newFakeLUN', is_snapshot=is_snapshot)

        clone_create_args = {
            'volume': 'volume',
            'source-path': 'fakeLUN',
            'destination-path': 'newFakeLUN',
            'space-reserve': 'true',
        }
        if is_snapshot and supports_is_backup:
            clone_create_args['is-backup'] = 'true'
        self.connection.invoke_successfully.assert_called_once_with(
            netapp_api.NaElement.create_node_with_children(
                'clone-create', **clone_create_args), True)

    def test_clone_lun_multiple_zapi_calls(self):
        """Test for when lun clone requires more than one zapi call."""

        # Max block-ranges per call = 32, max blocks per range = 2^24
        # Force 2 calls
        bc = 2 ** 24 * 32 * 2
        self.client.clone_lun('volume', 'fakeLUN', 'newFakeLUN',
                              block_count=bc)
        self.assertEqual(2, self.connection.invoke_successfully.call_count)

    def test_get_lun_by_args(self):
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>2</num-records>
                            <attributes-list>
                              <lun-info>
                              </lun-info>
                            </attributes-list>
                          </results>"""))
        self.connection.invoke_successfully.return_value = response

        lun = self.client.get_lun_by_args()

        self.assertEqual(1, len(lun))

    def test_get_lun_by_args_no_lun_found(self):
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>2</num-records>
                            <attributes-list>
                            </attributes-list>
                          </results>"""))
        self.connection.invoke_successfully.return_value = response

        lun = self.client.get_lun_by_args()

        self.assertEqual(0, len(lun))

    def test_get_lun_by_args_with_args_specified(self):
        path = '/vol/%s/%s' % (self.fake_volume, self.fake_lun)
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>2</num-records>
                            <attributes-list>
                              <lun-info>
                              </lun-info>
                            </attributes-list>
                          </results>"""))
        self.connection.invoke_successfully.return_value = response

        lun = self.client.get_lun_by_args(path=path)

        __, _args, __ = self.connection.invoke_successfully.mock_calls[0]
        actual_request = _args[0]
        query = actual_request.get_child_by_name('query')
        lun_info_args = query.get_child_by_name('lun-info').get_children()

        # Assert request is made with correct arguments
        self.assertEqual('path', lun_info_args[0].get_name())
        self.assertEqual(path, lun_info_args[0].get_content())

        self.assertEqual(1, len(lun))

    def test_file_assign_qos(self):

        api_args = {
            'volume': fake.FLEXVOL,
            'qos-policy-group-name': fake.QOS_POLICY_GROUP_NAME,
            'file': fake.NFS_FILE_PATH,
            'vserver': self.vserver
        }

        self.client.file_assign_qos(
            fake.FLEXVOL, fake.QOS_POLICY_GROUP_NAME, fake.NFS_FILE_PATH)

        self.mock_send_request.assert_has_calls([
            mock.call('file-assign-qos', api_args, False)])

    def test_set_lun_qos_policy_group(self):

        api_args = {
            'path': fake.LUN_PATH,
            'qos-policy-group': fake.QOS_POLICY_GROUP_NAME,
        }

        self.client.set_lun_qos_policy_group(
            fake.LUN_PATH, fake.QOS_POLICY_GROUP_NAME)

        self.mock_send_request.assert_has_calls([
            mock.call('lun-set-qos-policy-group', api_args)])

    def test_provision_qos_policy_group_no_qos_policy_group_info(self):

        self.client.provision_qos_policy_group(qos_policy_group_info=None)

        self.assertEqual(0, self.connection.qos_policy_group_create.call_count)

    def test_provision_qos_policy_group_legacy_qos_policy_group_info(self):

        self.client.provision_qos_policy_group(
            qos_policy_group_info=fake.QOS_POLICY_GROUP_INFO_LEGACY)

        self.assertEqual(0, self.connection.qos_policy_group_create.call_count)

    def test_provision_qos_policy_group_with_qos_spec_create(self):

        self.mock_object(self.client,
                         'qos_policy_group_exists',
                         mock.Mock(return_value=False))
        self.mock_object(self.client, 'qos_policy_group_create')
        self.mock_object(self.client, 'qos_policy_group_modify')

        self.client.provision_qos_policy_group(fake.QOS_POLICY_GROUP_INFO)

        self.client.qos_policy_group_create.assert_has_calls([
            mock.call(fake.QOS_POLICY_GROUP_NAME, fake.MAX_THROUGHPUT)])
        self.assertFalse(self.client.qos_policy_group_modify.called)

    def test_provision_qos_policy_group_with_qos_spec_modify(self):

        self.mock_object(self.client,
                         'qos_policy_group_exists',
                         mock.Mock(return_value=True))
        self.mock_object(self.client, 'qos_policy_group_create')
        self.mock_object(self.client, 'qos_policy_group_modify')

        self.client.provision_qos_policy_group(fake.QOS_POLICY_GROUP_INFO)

        self.assertFalse(self.client.qos_policy_group_create.called)
        self.client.qos_policy_group_modify.assert_has_calls([
            mock.call(fake.QOS_POLICY_GROUP_NAME, fake.MAX_THROUGHPUT)])

    def test_qos_policy_group_exists(self):

        self.mock_send_request.return_value = netapp_api.NaElement(
            fake_client.QOS_POLICY_GROUP_GET_ITER_RESPONSE)

        result = self.client.qos_policy_group_exists(
            fake.QOS_POLICY_GROUP_NAME)

        api_args = {
            'query': {
                'qos-policy-group-info': {
                    'policy-group': fake.QOS_POLICY_GROUP_NAME,
                },
            },
            'desired-attributes': {
                'qos-policy-group-info': {
                    'policy-group': None,
                },
            },
        }
        self.mock_send_request.assert_has_calls([
            mock.call('qos-policy-group-get-iter', api_args, False)])
        self.assertTrue(result)

    def test_qos_policy_group_exists_not_found(self):

        self.mock_send_request.return_value = netapp_api.NaElement(
            fake_client.NO_RECORDS_RESPONSE)

        result = self.client.qos_policy_group_exists(
            fake.QOS_POLICY_GROUP_NAME)

        self.assertFalse(result)

    def test_qos_policy_group_create(self):

        api_args = {
            'policy-group': fake.QOS_POLICY_GROUP_NAME,
            'max-throughput': fake.MAX_THROUGHPUT,
            'vserver': self.vserver,
        }

        self.client.qos_policy_group_create(
            fake.QOS_POLICY_GROUP_NAME, fake.MAX_THROUGHPUT)

        self.mock_send_request.assert_has_calls([
            mock.call('qos-policy-group-create', api_args, False)])

    def test_qos_policy_group_modify(self):

        api_args = {
            'policy-group': fake.QOS_POLICY_GROUP_NAME,
            'max-throughput': fake.MAX_THROUGHPUT,
        }

        self.client.qos_policy_group_modify(
            fake.QOS_POLICY_GROUP_NAME, fake.MAX_THROUGHPUT)

        self.mock_send_request.assert_has_calls([
            mock.call('qos-policy-group-modify', api_args, False)])

    def test_qos_policy_group_delete(self):

        api_args = {
            'policy-group': fake.QOS_POLICY_GROUP_NAME
        }

        self.client.qos_policy_group_delete(
            fake.QOS_POLICY_GROUP_NAME)

        self.mock_send_request.assert_has_calls([
            mock.call('qos-policy-group-delete', api_args, False)])

    def test_qos_policy_group_rename(self):

        new_name = 'new-' + fake.QOS_POLICY_GROUP_NAME
        api_args = {
            'policy-group-name': fake.QOS_POLICY_GROUP_NAME,
            'new-name': new_name,
        }

        self.client.qos_policy_group_rename(
            fake.QOS_POLICY_GROUP_NAME, new_name)

        self.mock_send_request.assert_has_calls([
            mock.call('qos-policy-group-rename', api_args, False)])

    def test_mark_qos_policy_group_for_deletion_no_qos_policy_group_info(self):

        mock_rename = self.mock_object(self.client, 'qos_policy_group_rename')
        mock_remove = self.mock_object(self.client,
                                       'remove_unused_qos_policy_groups')

        self.client.mark_qos_policy_group_for_deletion(
            qos_policy_group_info=None)

        self.assertEqual(0, mock_rename.call_count)
        self.assertEqual(0, mock_remove.call_count)

    def test_mark_qos_policy_group_for_deletion_legacy_qos_policy(self):

        mock_rename = self.mock_object(self.client, 'qos_policy_group_rename')
        mock_remove = self.mock_object(self.client,
                                       'remove_unused_qos_policy_groups')

        self.client.mark_qos_policy_group_for_deletion(
            qos_policy_group_info=fake.QOS_POLICY_GROUP_INFO_LEGACY)

        self.assertEqual(0, mock_rename.call_count)
        self.assertEqual(1, mock_remove.call_count)

    def test_mark_qos_policy_group_for_deletion_w_qos_spec(self):

        mock_rename = self.mock_object(self.client, 'qos_policy_group_rename')
        mock_remove = self.mock_object(self.client,
                                       'remove_unused_qos_policy_groups')
        mock_log = self.mock_object(client_cmode.LOG, 'warning')
        new_name = 'deleted_cinder_%s' % fake.QOS_POLICY_GROUP_NAME

        self.client.mark_qos_policy_group_for_deletion(
            qos_policy_group_info=fake.QOS_POLICY_GROUP_INFO)

        mock_rename.assert_has_calls([
            mock.call(fake.QOS_POLICY_GROUP_NAME, new_name)])
        self.assertEqual(0, mock_log.call_count)
        self.assertEqual(1, mock_remove.call_count)

    def test_mark_qos_policy_group_for_deletion_exception_path(self):

        mock_rename = self.mock_object(self.client, 'qos_policy_group_rename')
        mock_rename.side_effect = netapp_api.NaApiError
        mock_remove = self.mock_object(self.client,
                                       'remove_unused_qos_policy_groups')
        mock_log = self.mock_object(client_cmode.LOG, 'warning')
        new_name = 'deleted_cinder_%s' % fake.QOS_POLICY_GROUP_NAME

        self.client.mark_qos_policy_group_for_deletion(
            qos_policy_group_info=fake.QOS_POLICY_GROUP_INFO)

        mock_rename.assert_has_calls([
            mock.call(fake.QOS_POLICY_GROUP_NAME, new_name)])
        self.assertEqual(1, mock_log.call_count)
        self.assertEqual(1, mock_remove.call_count)

    def test_remove_unused_qos_policy_groups(self):

        mock_log = self.mock_object(client_cmode.LOG, 'debug')
        api_args = {
            'query': {
                'qos-policy-group-info': {
                    'policy-group': 'deleted_cinder_*',
                    'vserver': self.vserver,
                }
            },
            'max-records': 3500,
            'continue-on-failure': 'true',
            'return-success-list': 'false',
            'return-failure-list': 'false',
        }

        self.client.remove_unused_qos_policy_groups()

        self.mock_send_request.assert_has_calls([
            mock.call('qos-policy-group-delete-iter', api_args, False)])
        self.assertEqual(0, mock_log.call_count)

    def test_remove_unused_qos_policy_groups_api_error(self):

        mock_log = self.mock_object(client_cmode.LOG, 'debug')
        api_args = {
            'query': {
                'qos-policy-group-info': {
                    'policy-group': 'deleted_cinder_*',
                    'vserver': self.vserver,
                }
            },
            'max-records': 3500,
            'continue-on-failure': 'true',
            'return-success-list': 'false',
            'return-failure-list': 'false',
        }
        self.mock_send_request.side_effect = netapp_api.NaApiError

        self.client.remove_unused_qos_policy_groups()

        self.mock_send_request.assert_has_calls([
            mock.call('qos-policy-group-delete-iter', api_args, False)])
        self.assertEqual(1, mock_log.call_count)

    @mock.patch('cinder.volume.drivers.netapp.utils.resolve_hostname',
                return_value='192.168.1.101')
    def test_get_if_info_by_ip_not_found(self, mock_resolve_hostname):
        fake_ip = '192.168.1.101'
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>0</num-records>
                            <attributes-list>
                            </attributes-list>
                          </results>"""))
        self.connection.invoke_successfully.return_value = response

        self.assertRaises(exception.NotFound, self.client.get_if_info_by_ip,
                          fake_ip)

    @mock.patch('cinder.volume.drivers.netapp.utils.resolve_hostname',
                return_value='192.168.1.101')
    def test_get_if_info_by_ip(self, mock_resolve_hostname):
        fake_ip = '192.168.1.101'
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>1</num-records>
                            <attributes-list>
                                <net-interface-info>
                                </net-interface-info>
                            </attributes-list>
                          </results>"""))
        self.connection.invoke_successfully.return_value = response

        results = self.client.get_if_info_by_ip(fake_ip)

        self.assertEqual(1, len(results))

    def test_get_vol_by_junc_vserver_not_found(self):
        fake_vserver = 'fake_vserver'
        fake_junc = 'fake_junction_path'
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>0</num-records>
                            <attributes-list>
                            </attributes-list>
                          </results>"""))
        self.connection.invoke_successfully.return_value = response

        self.assertRaises(exception.NotFound,
                          self.client.get_vol_by_junc_vserver,
                          fake_vserver, fake_junc)

    def test_get_vol_by_junc_vserver(self):
        fake_vserver = 'fake_vserver'
        fake_junc = 'fake_junction_path'
        expected_flex_vol = 'fake_flex_vol'
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                            <num-records>1</num-records>
                            <attributes-list>
                              <volume-attributes>
                                <volume-id-attributes>
                                  <name>%(flex_vol)s</name>
                                </volume-id-attributes>
                              </volume-attributes>
                            </attributes-list>
                          </results>""" % {'flex_vol': expected_flex_vol}))
        self.connection.invoke_successfully.return_value = response

        actual_flex_vol = self.client.get_vol_by_junc_vserver(fake_vserver,
                                                              fake_junc)

        self.assertEqual(expected_flex_vol, actual_flex_vol)

    def test_clone_file(self):
        expected_flex_vol = "fake_flex_vol"
        expected_src_path = "fake_src_path"
        expected_dest_path = "fake_dest_path"
        self.connection.get_api_version.return_value = (1, 20)

        self.client.clone_file(expected_flex_vol, expected_src_path,
                               expected_dest_path, self.vserver)

        __, _args, __ = self.connection.invoke_successfully.mock_calls[0]
        actual_request = _args[0]
        actual_flex_vol = actual_request.get_child_by_name('volume') \
            .get_content()
        actual_src_path = actual_request \
            .get_child_by_name('source-path').get_content()
        actual_dest_path = actual_request.get_child_by_name(
            'destination-path').get_content()

        self.assertEqual(expected_flex_vol, actual_flex_vol)
        self.assertEqual(expected_src_path, actual_src_path)
        self.assertEqual(expected_dest_path, actual_dest_path)
        self.assertEqual(actual_request.get_child_by_name(
            'destination-exists'), None)

    def test_clone_file_when_destination_exists(self):
        expected_flex_vol = "fake_flex_vol"
        expected_src_path = "fake_src_path"
        expected_dest_path = "fake_dest_path"
        self.connection.get_api_version.return_value = (1, 20)

        self.client.clone_file(expected_flex_vol, expected_src_path,
                               expected_dest_path, self.vserver,
                               dest_exists=True)

        __, _args, __ = self.connection.invoke_successfully.mock_calls[0]
        actual_request = _args[0]
        actual_flex_vol = actual_request.get_child_by_name('volume') \
            .get_content()
        actual_src_path = actual_request \
            .get_child_by_name('source-path').get_content()
        actual_dest_path = actual_request.get_child_by_name(
            'destination-path').get_content()

        self.assertEqual(expected_flex_vol, actual_flex_vol)
        self.assertEqual(expected_src_path, actual_src_path)
        self.assertEqual(expected_dest_path, actual_dest_path)
        self.assertEqual('true',
                         actual_request.get_child_by_name(
                             'destination-exists').get_content())

    def test_clone_file_when_destination_exists_and_version_less_than_1_20(
            self):
        expected_flex_vol = "fake_flex_vol"
        expected_src_path = "fake_src_path"
        expected_dest_path = "fake_dest_path"
        self.connection.get_api_version.return_value = (1, 19)

        self.client.clone_file(expected_flex_vol, expected_src_path,
                               expected_dest_path, self.vserver,
                               dest_exists=True)

        __, _args, __ = self.connection.invoke_successfully.mock_calls[0]
        actual_request = _args[0]
        actual_flex_vol = actual_request.get_child_by_name('volume') \
            .get_content()
        actual_src_path = actual_request \
            .get_child_by_name('source-path').get_content()
        actual_dest_path = actual_request.get_child_by_name(
            'destination-path').get_content()

        self.assertEqual(expected_flex_vol, actual_flex_vol)
        self.assertEqual(expected_src_path, actual_src_path)
        self.assertEqual(expected_dest_path, actual_dest_path)
        self.assertIsNone(actual_request.get_child_by_name(
            'destination-exists'))

    @ddt.data({'supports_is_backup': True, 'is_snapshot': True},
              {'supports_is_backup': True, 'is_snapshot': False},
              {'supports_is_backup': False, 'is_snapshot': True},
              {'supports_is_backup': False, 'is_snapshot': False})
    @ddt.unpack
    def test_clone_file_is_snapshot(self, supports_is_backup, is_snapshot):

        self.connection.get_api_version.return_value = (1, 20)
        self.client.features.add_feature('BACKUP_CLONE_PARAM',
                                         supported=supports_is_backup)

        self.client.clone_file(
            'volume', 'fake_source', 'fake_destination', 'fake_vserver',
            is_snapshot=is_snapshot)

        clone_create_args = {
            'volume': 'volume',
            'source-path': 'fake_source',
            'destination-path': 'fake_destination',
        }
        if is_snapshot and supports_is_backup:
            clone_create_args['is-backup'] = 'true'
        self.connection.invoke_successfully.assert_called_once_with(
            netapp_api.NaElement.create_node_with_children(
                'clone-create', **clone_create_args), True)

    def test_get_file_usage(self):
        expected_bytes = "2048"
        fake_vserver = 'fake_vserver'
        fake_path = 'fake_path'
        response = netapp_api.NaElement(
            etree.XML("""<results status="passed">
                           <unique-bytes>%(unique-bytes)s</unique-bytes>
                         </results>""" % {'unique-bytes': expected_bytes}))
        self.connection.invoke_successfully.return_value = response

        actual_bytes = self.client.get_file_usage(fake_vserver, fake_path)

        self.assertEqual(expected_bytes, actual_bytes)

    def test_check_cluster_api(self):

        self.client.features.USER_CAPABILITY_LIST = True
        mock_check_cluster_api_legacy = self.mock_object(
            self.client, '_check_cluster_api_legacy')
        mock_check_cluster_api = self.mock_object(
            self.client, '_check_cluster_api', mock.Mock(return_value=True))

        result = self.client.check_cluster_api('object', 'operation', 'api')

        self.assertTrue(result)
        self.assertFalse(mock_check_cluster_api_legacy.called)
        mock_check_cluster_api.assert_called_once_with(
            'object', 'operation', 'api')

    def test_check_cluster_api_legacy(self):

        self.client.features.USER_CAPABILITY_LIST = False
        mock_check_cluster_api_legacy = self.mock_object(
            self.client, '_check_cluster_api_legacy',
            mock.Mock(return_value=True))
        mock_check_cluster_api = self.mock_object(
            self.client, '_check_cluster_api')

        result = self.client.check_cluster_api('object', 'operation', 'api')

        self.assertTrue(result)
        self.assertFalse(mock_check_cluster_api.called)
        mock_check_cluster_api_legacy.assert_called_once_with('api')

    def test__check_cluster_api(self):

        api_response = netapp_api.NaElement(
            fake_client.SYSTEM_USER_CAPABILITY_GET_ITER_RESPONSE)
        self.mock_send_request.return_value = api_response

        result = self.client._check_cluster_api('object', 'operation', 'api')

        system_user_capability_get_iter_args = {
            'query': {
                'capability-info': {
                    'object-name': 'object',
                    'operation-list': {
                        'operation-info': {
                            'name': 'operation',
                        },
                    },
                },
            },
            'desired-attributes': {
                'capability-info': {
                    'operation-list': {
                        'operation-info': {
                            'api-name': None,
                        },
                    },
                },
            },
        }
        self.mock_send_request.assert_called_once_with(
            'system-user-capability-get-iter',
            system_user_capability_get_iter_args,
            False)

        self.assertTrue(result)

    @ddt.data(fake_client.SYSTEM_USER_CAPABILITY_GET_ITER_RESPONSE,
              fake_client.NO_RECORDS_RESPONSE)
    def test__check_cluster_api_not_found(self, response):

        api_response = netapp_api.NaElement(response)
        self.mock_send_request.return_value = api_response

        result = self.client._check_cluster_api('object', 'operation', 'api4')

        self.assertFalse(result)

    @ddt.data('volume-get-iter', 'volume-get', 'aggr-options-list-info')
    def test__check_cluster_api_legacy(self, api):

        api_response = netapp_api.NaElement(fake_client.NO_RECORDS_RESPONSE)
        self.mock_send_request.return_value = api_response

        result = self.client._check_cluster_api_legacy(api)

        self.assertTrue(result)
        self.mock_send_request.assert_called_once_with(api,
                                                       enable_tunneling=False)

    @ddt.data(netapp_api.EAPIPRIVILEGE, netapp_api.EAPINOTFOUND)
    def test__check_cluster_api_legacy_insufficient_privileges(self, code):

        self.mock_send_request.side_effect = netapp_api.NaApiError(code=code)

        result = self.client._check_cluster_api_legacy('volume-get-iter')

        self.assertFalse(result)
        self.mock_send_request.assert_called_once_with('volume-get-iter',
                                                       enable_tunneling=False)

    def test__check_cluster_api_legacy_api_error(self):

        self.mock_send_request.side_effect = netapp_api.NaApiError()

        result = self.client._check_cluster_api_legacy('volume-get-iter')

        self.assertTrue(result)
        self.mock_send_request.assert_called_once_with('volume-get-iter',
                                                       enable_tunneling=False)

    def test__check_cluster_api_legacy_invalid_api(self):

        self.assertRaises(ValueError,
                          self.client._check_cluster_api_legacy,
                          'fake_api')

    def test_get_operational_lif_addresses(self):
        expected_result = ['1.2.3.4', '99.98.97.96']
        api_response = netapp_api.NaElement(
            fake_client.GET_OPERATIONAL_LIF_ADDRESSES_RESPONSE)
        self.mock_object(self.client,
                         'send_iter_request',
                         mock.Mock(return_value=api_response))

        address_list = self.client.get_operational_lif_addresses()

        net_interface_get_iter_args = {
            'query': {
                'net-interface-info': {
                    'operational-status': 'up'
                }
            },
            'desired-attributes': {
                'net-interface-info': {
                    'address': None,
                }
            }
        }
        self.client.send_iter_request.assert_called_once_with(
            'net-interface-get-iter', net_interface_get_iter_args)

        self.assertEqual(expected_result, address_list)

    @ddt.data({'flexvol_path': '/fake/vol'},
              {'flexvol_name': 'fake_volume'},
              {'flexvol_path': '/fake/vol', 'flexvol_name': 'fake_volume'})
    def test_get_flexvol_capacity(self, kwargs):

        api_response = netapp_api.NaElement(
            fake_client.VOLUME_GET_ITER_CAPACITY_RESPONSE)
        mock_send_iter_request = self.mock_object(
            self.client, 'send_iter_request',
            mock.Mock(return_value=api_response))

        capacity = self.client.get_flexvol_capacity(**kwargs)

        volume_id_attributes = {}
        if 'flexvol_path' in kwargs:
            volume_id_attributes['junction-path'] = kwargs['flexvol_path']
        if 'flexvol_name' in kwargs:
            volume_id_attributes['name'] = kwargs['flexvol_name']

        volume_get_iter_args = {
            'query': {
                'volume-attributes': {
                    'volume-id-attributes': volume_id_attributes,
                }
            },
            'desired-attributes': {
                'volume-attributes': {
                    'volume-space-attributes': {
                        'size-available': None,
                        'size-total': None,
                    }
                }
            },
        }
        mock_send_iter_request.assert_called_once_with(
            'volume-get-iter', volume_get_iter_args)

        self.assertEqual(fake_client.VOLUME_SIZE_TOTAL, capacity['size-total'])
        self.assertEqual(fake_client.VOLUME_SIZE_AVAILABLE,
                         capacity['size-available'])

    def test_get_flexvol_capacity_not_found(self):

        self.mock_send_request.return_value = netapp_api.NaElement(
            fake_client.NO_RECORDS_RESPONSE)

        self.assertRaises(exception.NetAppDriverException,
                          self.client.get_flexvol_capacity,
                          flexvol_path='fake_path')

    def test_list_flexvols(self):

        api_response = netapp_api.NaElement(
            fake_client.VOLUME_GET_ITER_LIST_RESPONSE)
        self.mock_object(self.client,
                         'send_iter_request',
                         mock.Mock(return_value=api_response))

        result = self.client.list_flexvols()

        volume_get_iter_args = {
            'query': {
                'volume-attributes': {
                    'volume-id-attributes': {
                        'type': 'rw',
                        'style': 'flex',
                    },
                    'volume-state-attributes': {
                        'is-vserver-root': 'false',
                        'is-inconsistent': 'false',
                        'is-invalid': 'false',
                        'state': 'online',
                    },
                },
            },
            'desired-attributes': {
                'volume-attributes': {
                    'volume-id-attributes': {
                        'name': None,
                    },
                },
            },
        }
        self.client.send_iter_request.assert_called_once_with(
            'volume-get-iter', volume_get_iter_args)
        self.assertEqual(list(fake_client.VOLUME_NAMES), result)

    def test_list_flexvols_not_found(self):

        api_response = netapp_api.NaElement(
            fake_client.NO_RECORDS_RESPONSE)
        self.mock_object(self.client,
                         'send_iter_request',
                         mock.Mock(return_value=api_response))

        result = self.client.list_flexvols()

        self.assertEqual([], result)

    def test_get_flexvol(self):

        api_response = netapp_api.NaElement(
            fake_client.VOLUME_GET_ITER_SSC_RESPONSE)
        self.mock_object(self.client,
                         'send_iter_request',
                         mock.Mock(return_value=api_response))

        result = self.client.get_flexvol(
            flexvol_name=fake_client.VOLUME_NAMES[0],
            flexvol_path='/%s' % fake_client.VOLUME_NAMES[0])

        volume_get_iter_args = {
            'query': {
                'volume-attributes': {
                    'volume-id-attributes': {
                        'name': fake_client.VOLUME_NAMES[0],
                        'junction-path': '/' + fake_client.VOLUME_NAMES[0],
                        'type': 'rw',
                        'style': 'flex',
                    },
                    'volume-state-attributes': {
                        'is-vserver-root': 'false',
                        'is-inconsistent': 'false',
                        'is-invalid': 'false',
                        'state': 'online',
                    },
                },
            },
            'desired-attributes': {
                'volume-attributes': {
                    'volume-id-attributes': {
                        'name': None,
                        'owning-vserver-name': None,
                        'junction-path': None,
                        'containing-aggregate-name': None,
                    },
                    'volume-mirror-attributes': {
                        'is-data-protection-mirror': None,
                        'is-replica-volume': None,
                    },
                    'volume-space-attributes': {
                        'is-space-guarantee-enabled': None,
                        'space-guarantee': None,
                    },
                    'volume-qos-attributes': {
                        'policy-group-name': None,
                    }
                },
            },
        }
        self.client.send_iter_request.assert_called_once_with(
            'volume-get-iter', volume_get_iter_args)
        self.assertEqual(fake_client.VOLUME_INFO_SSC, result)

    def test_get_flexvol_not_found(self):

        api_response = netapp_api.NaElement(
            fake_client.NO_RECORDS_RESPONSE)
        self.mock_object(self.client,
                         'send_iter_request',
                         mock.Mock(return_value=api_response))

        self.assertRaises(exception.VolumeBackendAPIException,
                          self.client.get_flexvol,
                          flexvol_name=fake_client.VOLUME_NAMES[0])

    def test_get_flexvol_dedupe_info(self):

        api_response = netapp_api.NaElement(
            fake_client.SIS_GET_ITER_SSC_RESPONSE)
        self.mock_object(self.client,
                         'send_iter_request',
                         mock.Mock(return_value=api_response))

        result = self.client.get_flexvol_dedupe_info(
            fake_client.VOLUME_NAMES[0])

        sis_get_iter_args = {
            'query': {
                'sis-status-info': {
                    'path': '/vol/%s' % fake_client.VOLUME_NAMES[0],
                },
            },
            'desired-attributes': {
                'sis-status-info': {
                    'state': None,
                    'is-compression-enabled': None,
                },
            },
        }
        self.client.send_iter_request.assert_called_once_with(
            'sis-get-iter', sis_get_iter_args)
        self.assertEqual(fake_client.VOLUME_DEDUPE_INFO_SSC, result)

    def test_get_flexvol_dedupe_info_not_found(self):

        api_response = netapp_api.NaElement(
            fake_client.NO_RECORDS_RESPONSE)
        self.mock_object(self.client,
                         'send_iter_request',
                         mock.Mock(return_value=api_response))

        result = self.client.get_flexvol_dedupe_info(
            fake_client.VOLUME_NAMES[0])

        expected = {'compression': False, 'dedupe': False}
        self.assertEqual(expected, result)

    def test_get_flexvol_dedupe_info_api_error(self):

        self.mock_object(self.client,
                         'send_iter_request',
                         mock.Mock(side_effect=self._mock_api_error()))

        result = self.client.get_flexvol_dedupe_info(
            fake_client.VOLUME_NAMES[0])

        expected = {'compression': False, 'dedupe': False}
        self.assertEqual(expected, result)

    def test_is_flexvol_mirrored(self):

        api_response = netapp_api.NaElement(
            fake_client.SNAPMIRROR_GET_ITER_RESPONSE)
        self.mock_object(self.client,
                         'send_iter_request',
                         mock.Mock(return_value=api_response))

        result = self.client.is_flexvol_mirrored(
            fake_client.VOLUME_NAMES[0], fake_client.VOLUME_VSERVER_NAME)

        snapmirror_get_iter_args = {
            'query': {
                'snapmirror-info': {
                    'source-vserver': fake_client.VOLUME_VSERVER_NAME,
                    'source-volume': fake_client.VOLUME_NAMES[0],
                    'mirror-state': 'snapmirrored',
                    'relationship-type': 'data_protection',
                },
            },
            'desired-attributes': {
                'snapmirror-info': None,
            },
        }
        self.client.send_iter_request.assert_called_once_with(
            'snapmirror-get-iter', snapmirror_get_iter_args)
        self.assertTrue(result)

    def test_is_flexvol_mirrored_not_mirrored(self):

        api_response = netapp_api.NaElement(
            fake_client.NO_RECORDS_RESPONSE)
        self.mock_object(self.client,
                         'send_request',
                         mock.Mock(return_value=api_response))

        result = self.client.is_flexvol_mirrored(
            fake_client.VOLUME_NAMES[0], fake_client.VOLUME_VSERVER_NAME)

        self.assertFalse(result)

    def test_is_flexvol_mirrored_api_error(self):

        self.mock_object(self.client,
                         'send_request',
                         mock.Mock(side_effect=self._mock_api_error()))

        result = self.client.is_flexvol_mirrored(
            fake_client.VOLUME_NAMES[0], fake_client.VOLUME_VSERVER_NAME)

        self.assertFalse(result)

    def test_get_aggregates(self):

        api_response = netapp_api.NaElement(
            fake_client.AGGR_GET_ITER_RESPONSE)
        self.mock_object(self.client,
                         'send_request',
                         mock.Mock(return_value=api_response))

        result = self.client._get_aggregates()

        self.client.send_request.assert_has_calls([
            mock.call('aggr-get-iter', {}, enable_tunneling=False)])
        self.assertListEqual(
            [aggr.to_string() for aggr in api_response.get_child_by_name(
                'attributes-list').get_children()],
            [aggr.to_string() for aggr in result])

    def test_get_aggregates_with_filters(self):

        api_response = netapp_api.NaElement(
            fake_client.AGGR_GET_SPACE_RESPONSE)
        self.mock_object(self.client,
                         'send_request',
                         mock.Mock(return_value=api_response))

        desired_attributes = {
            'aggr-attributes': {
                'aggregate-name': None,
                'aggr-space-attributes': {
                    'size-total': None,
                    'size-available': None,
                }
            }
        }

        result = self.client._get_aggregates(
            aggregate_names=fake_client.VOLUME_AGGREGATE_NAMES,
            desired_attributes=desired_attributes)

        aggr_get_iter_args = {
            'query': {
                'aggr-attributes': {
                    'aggregate-name': '|'.join(
                        fake_client.VOLUME_AGGREGATE_NAMES),
                }
            },
            'desired-attributes': desired_attributes
        }

        self.client.send_request.assert_has_calls([
            mock.call('aggr-get-iter', aggr_get_iter_args,
                      enable_tunneling=False)])
        self.assertListEqual(
            [aggr.to_string() for aggr in api_response.get_child_by_name(
                'attributes-list').get_children()],
            [aggr.to_string() for aggr in result])

    def test_get_aggregates_not_found(self):

        api_response = netapp_api.NaElement(fake_client.NO_RECORDS_RESPONSE)
        self.mock_object(self.client,
                         'send_request',
                         mock.Mock(return_value=api_response))

        result = self.client._get_aggregates()

        self.client.send_request.assert_has_calls([
            mock.call('aggr-get-iter', {}, enable_tunneling=False)])
        self.assertListEqual([], result)

    def test_get_node_for_aggregate(self):

        api_response = netapp_api.NaElement(
            fake_client.AGGR_GET_NODE_RESPONSE).get_child_by_name(
            'attributes-list').get_children()
        self.mock_object(self.client,
                         '_get_aggregates',
                         mock.Mock(return_value=api_response))

        result = self.client.get_node_for_aggregate(
            fake_client.VOLUME_AGGREGATE_NAME)

        desired_attributes = {
            'aggr-attributes': {
                'aggregate-name': None,
                'aggr-ownership-attributes': {
                    'home-name': None,
                },
            },
        }

        self.client._get_aggregates.assert_has_calls([
            mock.call(
                aggregate_names=[fake_client.VOLUME_AGGREGATE_NAME],
                desired_attributes=desired_attributes)])

        self.assertEqual(fake_client.NODE_NAME, result)

    def test_get_node_for_aggregate_none_requested(self):

        result = self.client.get_node_for_aggregate(None)

        self.assertIsNone(result)

    def test_get_node_for_aggregate_api_not_found(self):

        self.mock_object(self.client,
                         'send_request',
                         mock.Mock(side_effect=self._mock_api_error(
                             netapp_api.EAPINOTFOUND)))

        result = self.client.get_node_for_aggregate(
            fake_client.VOLUME_AGGREGATE_NAME)

        self.assertIsNone(result)

    def test_get_node_for_aggregate_api_error(self):

        self.mock_object(self.client, 'send_request', self._mock_api_error())

        self.assertRaises(netapp_api.NaApiError,
                          self.client.get_node_for_aggregate,
                          fake_client.VOLUME_AGGREGATE_NAME)

    def test_get_node_for_aggregate_not_found(self):

        api_response = netapp_api.NaElement(fake_client.NO_RECORDS_RESPONSE)
        self.mock_object(self.client,
                         'send_request',
                         mock.Mock(return_value=api_response))

        result = self.client.get_node_for_aggregate(
            fake_client.VOLUME_AGGREGATE_NAME)

        self.assertIsNone(result)

    def test_get_aggregate_none_specified(self):

        result = self.client.get_aggregate('')

        self.assertEqual({}, result)

    def test_get_aggregate(self):

        api_response = netapp_api.NaElement(
            fake_client.AGGR_GET_ITER_SSC_RESPONSE).get_child_by_name(
            'attributes-list').get_children()
        self.mock_object(self.client,
                         '_get_aggregates',
                         mock.Mock(return_value=api_response))

        result = self.client.get_aggregate(fake_client.VOLUME_AGGREGATE_NAME)

        desired_attributes = {
            'aggr-attributes': {
                'aggregate-name': None,
                'aggr-raid-attributes': {
                    'raid-type': None,
                },
            },
        }
        self.client._get_aggregates.assert_has_calls([
            mock.call(
                aggregate_names=[fake_client.VOLUME_AGGREGATE_NAME],
                desired_attributes=desired_attributes)])

        expected = {
            'name': fake_client.VOLUME_AGGREGATE_NAME,
            'raid-type': 'raid_dp',
        }
        self.assertEqual(expected, result)

    def test_get_aggregate_not_found(self):

        api_response = netapp_api.NaElement(fake_client.NO_RECORDS_RESPONSE)
        self.mock_object(self.client,
                         'send_request',
                         mock.Mock(return_value=api_response))

        result = self.client.get_aggregate(fake_client.VOLUME_AGGREGATE_NAME)

        self.assertEqual({}, result)

    def test_get_aggregate_api_error(self):

        self.mock_object(self.client,
                         'send_request',
                         mock.Mock(side_effect=self._mock_api_error()))

        result = self.client.get_aggregate(fake_client.VOLUME_AGGREGATE_NAME)

        self.assertEqual({}, result)

    def test_get_aggregate_disk_type(self):

        api_response = netapp_api.NaElement(
            fake_client.STORAGE_DISK_GET_ITER_RESPONSE)
        self.mock_object(self.client,
                         'send_request',
                         mock.Mock(return_value=api_response))

        result = self.client.get_aggregate_disk_type(
            fake_client.VOLUME_AGGREGATE_NAME)

        storage_disk_get_iter_args = {
            'max-records': 1,
            'query': {
                'storage-disk-info': {
                    'disk-raid-info': {
                        'disk-aggregate-info': {
                            'aggregate-name':
                            fake_client.VOLUME_AGGREGATE_NAME,
                        },
                    },
                },
            },
            'desired-attributes': {
                'storage-disk-info': {
                    'disk-raid-info': {
                        'effective-disk-type': None,
                    },
                },
            },
        }
        self.client.send_request.assert_called_once_with(
            'storage-disk-get-iter', storage_disk_get_iter_args,
            enable_tunneling=False)
        self.assertEqual(fake_client.AGGR_DISK_TYPE, result)

    @ddt.data(fake_client.NO_RECORDS_RESPONSE, fake_client.INVALID_RESPONSE)
    def test_get_aggregate_disk_type_not_found(self, response):

        api_response = netapp_api.NaElement(response)
        self.mock_object(self.client,
                         'send_request',
                         mock.Mock(return_value=api_response))

        result = self.client.get_aggregate_disk_type(
            fake_client.VOLUME_AGGREGATE_NAME)

        self.assertEqual('unknown', result)

    def test_get_aggregate_disk_type_api_error(self):

        self.mock_object(self.client,
                         'send_request',
                         mock.Mock(side_effect=self._mock_api_error()))

        result = self.client.get_aggregate_disk_type(
            fake_client.VOLUME_AGGREGATE_NAME)

        self.assertEqual('unknown', result)

    def test_get_aggregate_capacities(self):

        aggr1_capacities = {
            'percent-used': 50,
            'size-available': 100.0,
            'size-total': 200.0,
        }
        aggr2_capacities = {
            'percent-used': 75,
            'size-available': 125.0,
            'size-total': 500.0,
        }
        mock_get_aggregate_capacity = self.mock_object(
            self.client, 'get_aggregate_capacity',
            mock.Mock(side_effect=[aggr1_capacities, aggr2_capacities]))

        result = self.client.get_aggregate_capacities(['aggr1', 'aggr2'])

        expected = {
            'aggr1': aggr1_capacities,
            'aggr2': aggr2_capacities,
        }
        self.assertEqual(expected, result)
        mock_get_aggregate_capacity.assert_has_calls([
            mock.call('aggr1'),
            mock.call('aggr2'),
        ])

    def test_get_aggregate_capacities_not_found(self):

        mock_get_aggregate_capacity = self.mock_object(
            self.client, 'get_aggregate_capacity',
            mock.Mock(side_effect=[{}, {}]))

        result = self.client.get_aggregate_capacities(['aggr1', 'aggr2'])

        expected = {
            'aggr1': {},
            'aggr2': {},
        }
        self.assertEqual(expected, result)
        mock_get_aggregate_capacity.assert_has_calls([
            mock.call('aggr1'),
            mock.call('aggr2'),
        ])

    def test_get_aggregate_capacities_not_list(self):

        result = self.client.get_aggregate_capacities('aggr1')

        self.assertEqual({}, result)

    def test_get_aggregate_capacity(self):

        api_response = netapp_api.NaElement(
            fake_client.AGGR_GET_ITER_CAPACITY_RESPONSE).get_child_by_name(
            'attributes-list').get_children()
        self.mock_object(self.client,
                         '_get_aggregates',
                         mock.Mock(return_value=api_response))

        result = self.client.get_aggregate_capacity(
            fake_client.VOLUME_AGGREGATE_NAME)

        desired_attributes = {
            'aggr-attributes': {
                'aggr-space-attributes': {
                    'percent-used-capacity': None,
                    'size-available': None,
                    'size-total': None,
                },
            },
        }
        self.client._get_aggregates.assert_has_calls([
            mock.call(
                aggregate_names=[fake_client.VOLUME_AGGREGATE_NAME],
                desired_attributes=desired_attributes)])

        expected = {
            'percent-used': float(fake_client.AGGR_USED_PERCENT),
            'size-available': float(fake_client.AGGR_SIZE_AVAILABLE),
            'size-total': float(fake_client.AGGR_SIZE_TOTAL),
        }
        self.assertEqual(expected, result)

    def test_get_aggregate_capacity_not_found(self):

        api_response = netapp_api.NaElement(fake_client.NO_RECORDS_RESPONSE)
        self.mock_object(self.client,
                         'send_request',
                         mock.Mock(return_value=api_response))

        result = self.client.get_aggregate_capacity(
            fake_client.VOLUME_AGGREGATE_NAME)

        self.assertEqual({}, result)

    def test_get_aggregate_capacity_api_error(self):

        self.mock_object(self.client,
                         'send_request',
                         mock.Mock(side_effect=self._mock_api_error()))

        result = self.client.get_aggregate_capacity(
            fake_client.VOLUME_AGGREGATE_NAME)

        self.assertEqual({}, result)

    def test_get_performance_instance_uuids(self):

        self.mock_send_request.return_value = netapp_api.NaElement(
            fake_client.PERF_OBJECT_INSTANCE_LIST_INFO_ITER_RESPONSE)

        result = self.client.get_performance_instance_uuids(
            'system', fake_client.NODE_NAME)

        expected = [fake_client.NODE_NAME + ':kernel:system']
        self.assertEqual(expected, result)

        perf_object_instance_list_info_iter_args = {
            'objectname': 'system',
            'query': {
                'instance-info': {
                    'uuid': fake_client.NODE_NAME + ':*',
                }
            }
        }
        self.mock_send_request.assert_called_once_with(
            'perf-object-instance-list-info-iter',
            perf_object_instance_list_info_iter_args, enable_tunneling=False)

    def test_get_performance_counters(self):

        self.mock_send_request.return_value = netapp_api.NaElement(
            fake_client.PERF_OBJECT_GET_INSTANCES_SYSTEM_RESPONSE_CMODE)

        instance_uuids = [
            fake_client.NODE_NAMES[0] + ':kernel:system',
            fake_client.NODE_NAMES[1] + ':kernel:system',
        ]
        counter_names = ['avg_processor_busy']
        result = self.client.get_performance_counters('system',
                                                      instance_uuids,
                                                      counter_names)

        expected = [
            {
                'avg_processor_busy': '5674745133134',
                'instance-name': 'system',
                'instance-uuid': instance_uuids[0],
                'node-name': fake_client.NODE_NAMES[0],
                'timestamp': '1453412013',
            }, {
                'avg_processor_busy': '4077649009234',
                'instance-name': 'system',
                'instance-uuid': instance_uuids[1],
                'node-name': fake_client.NODE_NAMES[1],
                'timestamp': '1453412013'
            },
        ]
        self.assertEqual(expected, result)

        perf_object_get_instances_args = {
            'objectname': 'system',
            'instance-uuids': [
                {'instance-uuid': instance_uuid}
                for instance_uuid in instance_uuids
            ],
            'counters': [
                {'counter': counter} for counter in counter_names
            ],
        }
        self.mock_send_request.assert_called_once_with(
            'perf-object-get-instances', perf_object_get_instances_args,
            enable_tunneling=False)

    def test_check_iscsi_initiator_exists_when_no_initiator_exists(self):
        self.connection.invoke_successfully = mock.Mock(
            side_effect=netapp_api.NaApiError)
        initiator = fake_client.INITIATOR_IQN

        initiator_exists = self.client.check_iscsi_initiator_exists(initiator)

        self.assertFalse(initiator_exists)

    def test_check_iscsi_initiator_exists_when_initiator_exists(self):
        self.connection.invoke_successfully = mock.Mock()
        initiator = fake_client.INITIATOR_IQN

        initiator_exists = self.client.check_iscsi_initiator_exists(initiator)

        self.assertTrue(initiator_exists)

    def test_set_iscsi_chap_authentication_no_previous_initiator(self):
        self.connection.invoke_successfully = mock.Mock()
        self.mock_object(self.client, 'check_iscsi_initiator_exists',
                         mock.Mock(return_value=False))

        ssh = mock.Mock(paramiko.SSHClient)
        sshpool = mock.Mock(ssh_utils.SSHPool)
        self.client.ssh_client.ssh_pool = sshpool
        self.mock_object(self.client.ssh_client, 'execute_command_with_prompt')
        sshpool.item().__enter__ = mock.Mock(return_value=ssh)
        sshpool.item().__exit__ = mock.Mock(return_value=False)

        self.client.set_iscsi_chap_authentication(fake_client.INITIATOR_IQN,
                                                  fake_client.USER_NAME,
                                                  fake_client.PASSWORD)

        command = ('iscsi security create -vserver fake_vserver '
                   '-initiator-name iqn.2015-06.com.netapp:fake_iqn '
                   '-auth-type CHAP -user-name fake_user')
        self.client.ssh_client.execute_command_with_prompt.assert_has_calls(
            [mock.call(ssh, command, 'Password:', fake_client.PASSWORD)]
        )

    def test_set_iscsi_chap_authentication_with_preexisting_initiator(self):
        self.connection.invoke_successfully = mock.Mock()
        self.mock_object(self.client, 'check_iscsi_initiator_exists',
                         mock.Mock(return_value=True))

        ssh = mock.Mock(paramiko.SSHClient)
        sshpool = mock.Mock(ssh_utils.SSHPool)
        self.client.ssh_client.ssh_pool = sshpool
        self.mock_object(self.client.ssh_client, 'execute_command_with_prompt')
        sshpool.item().__enter__ = mock.Mock(return_value=ssh)
        sshpool.item().__exit__ = mock.Mock(return_value=False)

        self.client.set_iscsi_chap_authentication(fake_client.INITIATOR_IQN,
                                                  fake_client.USER_NAME,
                                                  fake_client.PASSWORD)

        command = ('iscsi security modify -vserver fake_vserver '
                   '-initiator-name iqn.2015-06.com.netapp:fake_iqn '
                   '-auth-type CHAP -user-name fake_user')
        self.client.ssh_client.execute_command_with_prompt.assert_has_calls(
            [mock.call(ssh, command, 'Password:', fake_client.PASSWORD)]
        )

    def test_set_iscsi_chap_authentication_with_ssh_exception(self):
        self.connection.invoke_successfully = mock.Mock()
        self.mock_object(self.client, 'check_iscsi_initiator_exists',
                         mock.Mock(return_value=True))

        ssh = mock.Mock(paramiko.SSHClient)
        sshpool = mock.Mock(ssh_utils.SSHPool)
        self.client.ssh_client.ssh_pool = sshpool
        sshpool.item().__enter__ = mock.Mock(return_value=ssh)
        sshpool.item().__enter__.side_effect = paramiko.SSHException(
            'Connection Failure')
        sshpool.item().__exit__ = mock.Mock(return_value=False)

        self.assertRaises(exception.VolumeBackendAPIException,
                          self.client.set_iscsi_chap_authentication,
                          fake_client.INITIATOR_IQN,
                          fake_client.USER_NAME,
                          fake_client.PASSWORD)

    def test_get_snapshot_if_snapshot_present_not_busy(self):
        expected_vol_name = fake.SNAPSHOT['volume_id']
        expected_snapshot_name = fake.SNAPSHOT['name']
        response = netapp_api.NaElement(
            fake_client.SNAPSHOT_INFO_FOR_PRESENT_NOT_BUSY_SNAPSHOT_CMODE)
        self.mock_send_request.return_value = response

        snapshot = self.client.get_snapshot(expected_vol_name,
                                            expected_snapshot_name)

        self.assertEqual(expected_vol_name, snapshot['volume'])
        self.assertEqual(expected_snapshot_name, snapshot['name'])
        self.assertEqual(set([]), snapshot['owners'])
        self.assertFalse(snapshot['busy'])

    def test_get_snapshot_if_snapshot_present_busy(self):
        expected_vol_name = fake.SNAPSHOT['volume_id']
        expected_snapshot_name = fake.SNAPSHOT['name']
        response = netapp_api.NaElement(
            fake_client.SNAPSHOT_INFO_FOR_PRESENT_BUSY_SNAPSHOT_CMODE)
        self.mock_send_request.return_value = response

        snapshot = self.client.get_snapshot(expected_vol_name,
                                            expected_snapshot_name)

        self.assertEqual(expected_vol_name, snapshot['volume'])
        self.assertEqual(expected_snapshot_name, snapshot['name'])
        self.assertEqual(set([]), snapshot['owners'])
        self.assertTrue(snapshot['busy'])

    def test_get_snapshot_if_snapshot_not_present(self):
        expected_vol_name = fake.SNAPSHOT['volume_id']
        expected_snapshot_name = fake.SNAPSHOT['name']
        response = netapp_api.NaElement(fake_client.NO_RECORDS_RESPONSE)
        self.mock_send_request.return_value = response

        self.assertRaises(exception.SnapshotNotFound, self.client.get_snapshot,
                          expected_vol_name, expected_snapshot_name)
