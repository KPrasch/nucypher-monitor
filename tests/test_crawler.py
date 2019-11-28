import os
from unittest.mock import MagicMock, patch

import maya
import pytest

from nucypher.blockchain.economics import StandardTokenEconomics
from nucypher.blockchain.eth.agents import StakingEscrowAgent, EthereumContractAgent
from nucypher.blockchain.eth.registry import InMemoryContractRegistry, BaseContractRegistry
from nucypher.blockchain.eth.token import NU
from nucypher.blockchain.eth.utils import datetime_to_period
from nucypher.cli import actions
from nucypher.config.storages import SQLiteForgetfulNodeStorage
from nucypher.network.middleware import RestMiddleware

import monitor
from monitor.crawler import CrawlerNodeStorage, Crawler
from monitor.db import CrawlerNodeMetadataDBClient
from tests.utilities import (
    create_random_mock_node,
    create_specific_mock_node,
    create_specific_mock_state,
    verify_mock_node_matches,
    verify_mock_state_matches
)

IN_MEMORY_FILEPATH = ':memory:'
DB_TABLES = [CrawlerNodeStorage.NODE_DB_NAME, CrawlerNodeStorage.STATE_DB_NAME, CrawlerNodeStorage.TEACHER_DB_NAME]


#
# CrawlerNodeStorage tests.
#
def verify_all_db_tables_exist(db_conn, expect_present=True):
    # check tables created
    result = db_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    if not expect_present:
        assert len(result) == 0
    else:
        for row in result:
            assert row[0] in DB_TABLES


def verify_all_db_tables(db_conn, expect_empty=True):
    for table in DB_TABLES:
        result = db_conn.execute(f"SELECT * FROM {table}").fetchall()
        if expect_empty:
            assert len(result) == 0
        else:
            assert len(result) > 0


def verify_current_teacher(db_conn, expected_teacher_checksum):
    result = db_conn.execute(f"SELECT checksum_address from {CrawlerNodeStorage.TEACHER_DB_NAME}").fetchall()
    assert len(result) == 1
    for row in result:
        assert expected_teacher_checksum == row[0]


def test_storage_init():
    node_storage = CrawlerNodeStorage(db_filepath=IN_MEMORY_FILEPATH)
    assert node_storage.db_filepath == IN_MEMORY_FILEPATH
    assert not node_storage.federated_only
    assert CrawlerNodeStorage._name != SQLiteForgetfulNodeStorage._name


def test_storage_db_table_init():
    node_storage = CrawlerNodeStorage(db_filepath=IN_MEMORY_FILEPATH)

    verify_all_db_tables_exist(node_storage.db_conn)


def test_storage_initialize():
    node_storage = CrawlerNodeStorage(db_filepath=IN_MEMORY_FILEPATH)

    node_storage.initialize()  # re-initialize
    verify_all_db_tables_exist(node_storage.db_conn)


def test_storage_store_node_metadata_store():
    node_storage = CrawlerNodeStorage(db_filepath=IN_MEMORY_FILEPATH)

    node = create_specific_mock_node()

    # Store node data
    node_storage.store_node_metadata(node=node)

    result = node_storage.db_conn.execute(f"SELECT * FROM {CrawlerNodeStorage.NODE_DB_NAME}").fetchall()
    assert len(result) == 1
    for row in result:
        verify_mock_node_matches(node, row)

    # update node timestamp value and store
    new_now = node.timestamp.add(hours=1)
    worker_address = '0xabcdef'
    updated_node = create_specific_mock_node(timestamp=new_now, worker_address=worker_address)

    # ensure same item gets updated
    node_storage.store_node_metadata(node=updated_node)
    result = node_storage.db_conn.execute(f"SELECT * FROM {CrawlerNodeStorage.NODE_DB_NAME}").fetchall()
    assert len(result) == 1  # node data is updated not added
    for row in result:
        verify_mock_node_matches(updated_node, row)


def test_storage_store_state_metadata_store():
    node_storage = CrawlerNodeStorage(db_filepath=IN_MEMORY_FILEPATH)

    state = create_specific_mock_state()

    # Store state data
    node_storage.store_state_metadata(state=state)

    result = node_storage.db_conn.execute(f"SELECT * FROM {CrawlerNodeStorage.STATE_DB_NAME}").fetchall()
    assert len(result) == 1
    for row in result:
        verify_mock_state_matches(state, row)

    # update state
    new_now = state.updated.add(minutes=5)
    new_color = 'red'
    new_color_hex = '4F3D21'
    symbol = '%'
    updated_state = create_specific_mock_state(updated=new_now, color=new_color, color_hex=new_color_hex, symbol=symbol)
    node_storage.store_state_metadata(state=updated_state)

    # ensure same item gets updated
    result = node_storage.db_conn.execute(f"SELECT * FROM {CrawlerNodeStorage.STATE_DB_NAME}").fetchall()
    assert len(result) == 1  # state data is updated not added
    for row in result:
        verify_mock_state_matches(updated_state, row)


def test_storage_store_current_retrieval():
    node_storage = CrawlerNodeStorage(db_filepath=IN_MEMORY_FILEPATH)

    teacher_checksum = '0x123456789'
    node_storage.store_current_teacher(teacher_checksum=teacher_checksum)
    # check current teacher
    verify_current_teacher(node_storage.db_conn, teacher_checksum)

    # update current teacher
    updated_teacher_checksum = '0x987654321'
    node_storage.store_current_teacher(teacher_checksum=updated_teacher_checksum)
    # check current teacher
    verify_current_teacher(node_storage.db_conn, updated_teacher_checksum)


def test_storage_deletion(tempfile_path):
    assert os.path.exists(tempfile_path)

    node_storage = CrawlerNodeStorage(db_filepath=tempfile_path)
    del node_storage

    assert not os.path.exists(tempfile_path)  # db file deleted


def test_storage_db_clear():
    node_storage = CrawlerNodeStorage(db_filepath=IN_MEMORY_FILEPATH)
    verify_all_db_tables_exist(node_storage.db_conn)

    # store some data
    node = create_random_mock_node()
    node_storage.store_node_metadata(node=node)

    state = create_specific_mock_state()
    node_storage.store_state_metadata(state=state)

    teacher_checksum = '0x123456789'
    node_storage.store_current_teacher(teacher_checksum)

    verify_all_db_tables(node_storage.db_conn, expect_empty=False)

    # clear tables
    node_storage.clear()

    # db tables should have been cleared
    verify_all_db_tables(node_storage.db_conn, expect_empty=True)


def test_storage_db_clear_only_metadata_not_certificates():
    node_storage = CrawlerNodeStorage(db_filepath=IN_MEMORY_FILEPATH)

    # store some data
    node = create_random_mock_node()
    node_storage.store_node_metadata(node=node)

    state = create_specific_mock_state()
    node_storage.store_state_metadata(state=state)

    teacher_checksum = '0x123456789'
    node_storage.store_current_teacher(teacher_checksum)

    verify_all_db_tables(node_storage.db_conn, expect_empty=False)

    # clear metadata tables
    node_storage.clear(metadata=True, certificates=False)

    # db tables should have been cleared
    verify_all_db_tables(node_storage.db_conn, expect_empty=True)


def test_storage_db_clear_not_metadata():
    node_storage = CrawlerNodeStorage(db_filepath=IN_MEMORY_FILEPATH)

    # store some data
    node = create_random_mock_node()
    node_storage.store_node_metadata(node=node)

    state = create_specific_mock_state()
    node_storage.store_state_metadata(state=state)

    teacher_checksum = '0x123456789'
    node_storage.store_current_teacher(teacher_checksum)

    verify_all_db_tables(node_storage.db_conn, expect_empty=False)

    # only clear certificates data
    node_storage.clear(metadata=False, certificates=True)

    # db tables should not have been cleared
    verify_all_db_tables(node_storage.db_conn, expect_empty=False)


#
# Crawler tests.
#

class MockContractAgency:
    def __init__(self, staking_agent=MagicMock(spec=StakingEscrowAgent)):
        self.staking_agent = staking_agent

    def get_agent(self, agent_class, registry: BaseContractRegistry, provider_uri: str = None):
        if agent_class == StakingEscrowAgent:
            return self.staking_agent
        else:
            return MagicMock(spec=agent_class)


def create_crawler(node_db_filepath: str = IN_MEMORY_FILEPATH, dont_set_teacher: bool = False):
    registry = InMemoryContractRegistry()
    middleware = RestMiddleware()
    teacher_nodes = None
    if not dont_set_teacher:
        teacher_nodes = actions.load_seednodes(None,
                                               teacher_uris=['https://discover.nucypher.network:9151'],
                                               min_stake=0,
                                               federated_only=False,
                                               network_domains={'goerli'},
                                               network_middleware=middleware)

    crawler = Crawler(domains={'goerli'},
                      network_middleware=middleware,
                      known_nodes=teacher_nodes,
                      registry=registry,
                      start_learning_now=True,
                      learn_on_same_thread=False,
                      blockchain_db_host='localhost',
                      blockchain_db_port=8086,
                      node_db_filepath=node_db_filepath
                      )
    return crawler


def configure_mock_staking_agent(staking_agent, tokens, current_period, initial_period,
                                 terminal_period, last_active_period):
    staking_agent.owned_tokens.return_value = tokens
    staking_agent.get_locked_tokens.return_value = tokens

    staking_agent.get_current_period.return_value = current_period
    staking_agent.get_all_stakes.return_value = [(initial_period, terminal_period, tokens)]
    staking_agent.get_last_active_period.return_value = last_active_period


@patch.object(monitor.crawler.ContractAgency, 'get_agent', autospec=True)
def test_crawler_init(get_agent):
    staking_agent = MagicMock(spec=StakingEscrowAgent)
    contract_agency = MockContractAgency(staking_agent=staking_agent)
    get_agent.side_effect = contract_agency.get_agent

    crawler = create_crawler()

    # crawler not yet started
    assert not crawler.is_running


# TODO: weird patching issue where a TypeError is returned if this test isn't before any patching of InfluxDBClient
#  since it uses the actual InfluxDBClient class
@patch.object(monitor.crawler.ContractAgency, 'get_agent', autospec=True)
def test_crawler_start_no_influx_db_connection(get_agent):
    staking_agent = MagicMock(spec=StakingEscrowAgent)
    contract_agency = MockContractAgency(staking_agent=staking_agent)
    get_agent.side_effect = contract_agency.get_agent

    crawler = create_crawler()
    try:
        with pytest.raises(ConnectionError):
            crawler.start()
    finally:
        crawler.stop()


@patch.object(monitor.crawler.ContractAgency, 'get_agent', autospec=True)
@patch.object(monitor.crawler.InfluxDBClient, '__new__', autospec=True)
def test_crawler_stop_before_start(new_influx_db, get_agent):
    mock_influxdb_client = MagicMock(spec=monitor.crawler.InfluxDBClient, autospec=True)
    new_influx_db.return_value = mock_influxdb_client

    staking_agent = MagicMock(spec=StakingEscrowAgent)
    contract_agency = MockContractAgency(staking_agent=staking_agent)
    get_agent.side_effect = contract_agency.get_agent

    crawler = create_crawler()

    crawler.stop()

    new_influx_db.assert_not_called()  # db only initialized when crawler is started
    mock_influxdb_client.close.assert_not_called()  # just to be sure
    assert not crawler.is_running


@patch.object(monitor.crawler.ContractAgency, 'get_agent', autospec=True)
@patch.object(monitor.crawler.InfluxDBClient, '__new__', autospec=True)
def test_crawler_start_then_stop(new_influx_db, get_agent):
    mock_influxdb_client = MagicMock(spec=monitor.crawler.InfluxDBClient, autospec=True)
    new_influx_db.return_value = mock_influxdb_client

    staking_agent = MagicMock(spec=StakingEscrowAgent)
    contract_agency = MockContractAgency(staking_agent=staking_agent)
    get_agent.side_effect = contract_agency.get_agent

    crawler = create_crawler()
    try:
        crawler.start()
        assert crawler.is_running
        mock_influxdb_client.close.assert_not_called()
    finally:
        crawler.stop()

    mock_influxdb_client.close.assert_called_once()
    assert not crawler.is_running


@patch.object(monitor.crawler.ContractAgency, 'get_agent', autospec=True)
@patch.object(monitor.crawler.InfluxDBClient, '__new__', autospec=True)
def test_crawler_start_blockchain_db_not_present(new_influx_db, get_agent):
    mock_influxdb_client = MagicMock(spec=monitor.crawler.InfluxDBClient, autospec=True)
    mock_influxdb_client.get_list_database.return_value = [{'name': 'db1'},
                                                           {'name': 'db2'},
                                                           {'name': 'db3'}]
    new_influx_db.return_value = mock_influxdb_client

    staking_agent = MagicMock(spec=StakingEscrowAgent)
    contract_agency = MockContractAgency(staking_agent=staking_agent)
    get_agent.side_effect = contract_agency.get_agent

    crawler = create_crawler()
    try:
        crawler.start()
        assert crawler.is_running
        mock_influxdb_client.close.assert_not_called()

        # ensure table existence check run
        mock_influxdb_client.get_list_database.assert_called_once()
        # db created since not present
        mock_influxdb_client.create_database.assert_called_once_with(Crawler.BLOCKCHAIN_DB_NAME)
        mock_influxdb_client.create_retention_policy.assert_called_once()
    finally:
        crawler.stop()

    mock_influxdb_client.close.assert_called_once()
    assert not crawler.is_running


@patch.object(monitor.crawler.ContractAgency, 'get_agent', autospec=True)
@patch.object(monitor.crawler.InfluxDBClient, '__new__', autospec=True)
def test_crawler_start_blockchain_db_already_present(new_influx_db, get_agent):
    mock_influxdb_client = MagicMock(spec=monitor.crawler.InfluxDBClient, autospec=True)
    mock_influxdb_client.get_list_database.return_value = [{'name': 'db1'},
                                                           {'name': f'{Crawler.BLOCKCHAIN_DB_NAME}'},
                                                           {'name': 'db3'}]
    new_influx_db.return_value = mock_influxdb_client

    staking_agent = MagicMock(spec=StakingEscrowAgent)
    contract_agency = MockContractAgency(staking_agent=staking_agent)
    get_agent.side_effect = contract_agency.get_agent

    crawler = create_crawler()
    try:
        crawler.start()
        assert crawler.is_running
        mock_influxdb_client.close.assert_not_called()

        # ensure table existence check run
        mock_influxdb_client.get_list_database.assert_called_once()
        # db not created since not present
        mock_influxdb_client.create_database.assert_not_called()
        mock_influxdb_client.create_retention_policy.assert_not_called()
    finally:
        crawler.stop()

    mock_influxdb_client.close.assert_called_once()
    assert not crawler.is_running


@patch.object(monitor.crawler.ContractAgency, 'get_agent', autospec=True)
@patch.object(monitor.crawler.InfluxDBClient, '__new__', autospec=True)
def test_crawler_learn_no_teacher(new_influx_db, get_agent, tempfile_path):
    mock_influxdb_client = MagicMock(spec=monitor.crawler.InfluxDBClient, autospec=True)
    new_influx_db.return_value = mock_influxdb_client

    staking_agent = MagicMock(spec=StakingEscrowAgent)
    contract_agency = MockContractAgency(staking_agent=staking_agent)
    get_agent.side_effect = contract_agency.get_agent

    crawler = create_crawler(node_db_filepath=tempfile_path, dont_set_teacher=True)
    node_db_client = CrawlerNodeMetadataDBClient(db_filepath=tempfile_path)
    try:
        crawler.start()
        assert crawler.is_running

        # learn about teacher
        crawler.learn_from_teacher_node()

        known_nodes = node_db_client.get_known_nodes_metadata()
        assert len(known_nodes) == 0

        current_teacher_checksum = node_db_client.get_current_teacher_checksum()
        assert current_teacher_checksum is None
    finally:
        crawler.stop()

    mock_influxdb_client.close.assert_called_once()
    assert not crawler.is_running


@patch.object(monitor.crawler.ContractAgency, 'get_agent', autospec=True)
@patch.object(monitor.crawler.InfluxDBClient, '__new__', autospec=True)
def test_crawler_learn_about_teacher(new_influx_db, get_agent, tempfile_path):
    mock_influxdb_client = MagicMock(spec=monitor.crawler.InfluxDBClient, autospec=True)
    new_influx_db.return_value = mock_influxdb_client

    staking_agent = MagicMock(spec=StakingEscrowAgent)
    contract_agency = MockContractAgency(staking_agent=staking_agent)
    get_agent.side_effect = contract_agency.get_agent

    crawler = create_crawler(node_db_filepath=tempfile_path)
    node_db_client = CrawlerNodeMetadataDBClient(db_filepath=tempfile_path)
    try:
        crawler.start()
        assert crawler.is_running

        # learn about teacher
        crawler.learn_from_teacher_node()

        current_teacher_checksum = node_db_client.get_current_teacher_checksum()
        assert current_teacher_checksum is not None

        known_nodes = node_db_client.get_known_nodes_metadata()
        assert len(known_nodes) > 0
        assert current_teacher_checksum in known_nodes
    finally:
        crawler.stop()

    mock_influxdb_client.close.assert_called_once()
    assert not crawler.is_running


@patch.object(monitor.crawler.TokenEconomicsFactory, 'get_economics', autospec=True)
@patch.object(monitor.crawler.ContractAgency, 'get_agent', autospec=True)
@patch.object(monitor.crawler.InfluxDBClient, '__new__', autospec=True)
def test_crawler_learn_about_nodes(new_influx_db, get_agent, get_economics, tempfile_path):
    mock_influxdb_client = MagicMock(spec=monitor.crawler.InfluxDBClient, autospec=True)
    new_influx_db.return_value = mock_influxdb_client
    mock_influxdb_client.write_points.return_value = True

    # TODO: issue with use of `agent.blockchain` causes spec=StakingEscrowAgent not to be specified in MagicMock
    # Get the following - AttributeError: Mock object has no attribute 'blockchain'
    staking_agent = MagicMock(autospec=True)
    contract_agency = MockContractAgency(staking_agent=staking_agent)
    get_agent.side_effect = contract_agency.get_agent

    token_economics = StandardTokenEconomics()
    get_economics.return_value = token_economics

    crawler = create_crawler(node_db_filepath=tempfile_path)
    node_db_client = CrawlerNodeMetadataDBClient(db_filepath=tempfile_path)
    try:
        crawler.start()
        assert crawler.is_running

        for i in range(0, 5):
            random_node = create_random_mock_node(generate_certificate=True)
            crawler.remember_node(node=random_node, force_verification_check=False, record_fleet_state=True)
            known_nodes = node_db_client.get_known_nodes_metadata()
            assert len(known_nodes) > i
            assert random_node.checksum_address in known_nodes

            previous_states = node_db_client.get_previous_states_metadata()
            assert len(previous_states) > i

            # configure staking agent for blockchain calls
            tokens = 15000 + i*5
            current_period = datetime_to_period(maya.now(), token_economics.seconds_per_period)
            initial_period = current_period - i
            terminal_period = current_period + (i+50)
            last_active_period = current_period - i
            staking_agent.get_worker_from_staker.side_effect = \
                lambda staker_address: crawler.node_storage.get(federated_only=False,
                                                                checksum_address=staker_address).worker_address

            configure_mock_staking_agent(staking_agent=staking_agent,
                                         tokens=tokens,
                                         current_period=current_period,
                                         initial_period=initial_period,
                                         terminal_period=terminal_period,
                                         last_active_period=last_active_period)

            # run crawler callable
            crawler._learn_about_nodes_contract_info()

            # ensure data written to influx table
            mock_influxdb_client.write_points.assert_called_once()

            # expected db row added
            write_points_call_args_list = mock_influxdb_client.write_points.call_args_list
            influx_db_line_protocol_statement = str(write_points_call_args_list[0][0])

            expected_arguments = [f'staker_address={random_node.checksum_address}',
                                  f'worker_address="{random_node.worker_address}"',
                                  f'stake={float(NU.from_nunits(tokens).to_tokens())}',
                                  f'locked_stake={float(NU.from_nunits(tokens).to_tokens())}',
                                  f'current_period={current_period}i',
                                  f'last_confirmed_period={last_active_period}i']
            for arg in expected_arguments:
                assert arg in influx_db_line_protocol_statement, \
                    f"{arg} in {influx_db_line_protocol_statement} for iteration {i}"

            mock_influxdb_client.reset_mock()
    finally:
        crawler.stop()

    mock_influxdb_client.close.assert_called_once()
    assert not crawler.is_running
