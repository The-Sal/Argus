#!/usr/bin/env python3
"""
Test backward compatibility of P2 versioning with existing parsers and dispatchers.

This test simulates:
1. Old parsers receiving new versioned packets (should ignore version field)
2. New parsers receiving old non-versioned packets (should default to version 1)
3. Mixed version scenarios in a multi-packet stream
"""

import time
from argus.capital._svr_utils import Protocol2Parser, transmit_mkt_data_with_protocol_2
from argus.capital import CapitalComMKTDataLive
from argus.ib._ib_utils import IBKR_CapitalComMKTDataLive


def test_old_parser_with_new_packets():
    """
    Test that an 'old' parser (ignoring extra fields) can still parse new packets.
    
    In reality, the Protocol2Parser we've updated will detect the version,
    but we're simulating how an old parser would behave - it would just
    ignore extra data beyond its expected field count.
    """
    print("\n=== Test Old Parser with New Versioned Packets ===")
    
    mkt_data = CapitalComMKTDataLive(
        symbol='BTCUSD',
        bid=50000.0,
        bid_size=1.0,
        ask=50001.0,
        ask_size=1.0,
        last=50000.5,
        last_size=1.0,
        timestamp=int(time.time())
    )
    
    # Create a version 2 packet
    packet_v2 = transmit_mkt_data_with_protocol_2(mkt_data, version=2)
    
    # Parse with our parser (simulates new parser that handles version)
    parser = Protocol2Parser([
        'bid', 'bid_size', 'ask', 'ask_size',
        'last', 'last_size', 'timestamp', 'transmission_time'
    ])
    
    result = parser.parse(packet_v2)
    
    # Verify all standard fields are present
    assert result['symbol'] == 'BTCUSD'
    assert result['bid'] == 50000.0
    assert result['ask'] == 50001.0
    assert result['_p2_version'] == 2
    
    print(f"✓ Version 2 packet parsed successfully: {result['symbol']}, version={result['_p2_version']}")


def test_new_parser_with_old_packets():
    """
    Test that a new parser can handle old packets without version field.
    """
    print("\n=== Test New Parser with Old Non-Versioned Packets ===")
    
    mkt_data = CapitalComMKTDataLive(
        symbol='ETHUSD',
        bid=3000.0,
        bid_size=5.0,
        ask=3001.0,
        ask_size=5.0,
        last=3000.5,
        last_size=2.5,
        timestamp=int(time.time())
    )
    
    # Create a version 1 packet (no version field)
    packet_v1 = transmit_mkt_data_with_protocol_2(mkt_data, version=1)
    
    # Parse with new parser
    parser = Protocol2Parser([
        'bid', 'bid_size', 'ask', 'ask_size',
        'last', 'last_size', 'timestamp', 'transmission_time'
    ])
    
    result = parser.parse(packet_v1)
    
    # Verify version defaults to 1
    assert result['_p2_version'] == 1
    assert result['symbol'] == 'ETHUSD'
    assert result['bid'] == 3000.0
    
    print(f"✓ Version 1 packet parsed successfully: {result['symbol']}, version={result['_p2_version']}")


def test_ibkr_extended_class():
    """
    Test that the IBKR extended class (with shortable_shares) works with versioning.
    """
    print("\n=== Test IBKR Extended Class with Versioning ===")
    
    # Create IBKR market data with shortable_shares
    ibkr_data = IBKR_CapitalComMKTDataLive(
        shortable_shares=10000,
        symbol='AAPL',
        bid=150.0,
        bid_size=100.0,
        ask=151.0,
        ask_size=100.0,
        last=150.5,
        last_size=50.0,
        timestamp=int(time.time())
    )
    
    # Test version 1
    packet_v1 = transmit_mkt_data_with_protocol_2(ibkr_data, version=1)
    parser = Protocol2Parser([
        'bid', 'bid_size', 'ask', 'ask_size',
        'last', 'last_size', 'shortable_shares',
        'timestamp', 'transmission_time'
    ])
    
    result_v1 = parser.parse(packet_v1)
    assert result_v1['_p2_version'] == 1
    assert result_v1['symbol'] == 'AAPL'
    assert result_v1['shortable_shares'] == 10000.0
    
    # Test version 2
    packet_v2 = transmit_mkt_data_with_protocol_2(ibkr_data, version=2)
    result_v2 = parser.parse(packet_v2)
    assert result_v2['_p2_version'] == 2
    assert result_v2['symbol'] == 'AAPL'
    assert result_v2['shortable_shares'] == 10000.0
    
    print(f"✓ IBKR extended class works with both versions")


def test_stream_simulation():
    """
    Simulate a stream with mixed version packets as might happen during
    a rolling upgrade of dispatchers.
    """
    print("\n=== Test Mixed Version Stream Simulation ===")
    
    # Create multiple market data objects
    data_points = [
        CapitalComMKTDataLive('BTC', 50000.0, 1.0, 50001.0, 1.0, 50000.5, 1.0, int(time.time())),
        CapitalComMKTDataLive('ETH', 3000.0, 5.0, 3001.0, 5.0, 3000.5, 2.5, int(time.time())),
        CapitalComMKTDataLive('SOL', 100.0, 10.0, 101.0, 10.0, 100.5, 5.0, int(time.time())),
        CapitalComMKTDataLive('ADA', 1.0, 1000.0, 1.01, 1000.0, 1.005, 500.0, int(time.time())),
    ]
    
    # Create stream with mixed versions (simulating gradual rollout)
    stream = b''
    for i, data in enumerate(data_points):
        # First two use v1, last two use v2
        version = 1 if i < 2 else 2
        stream += transmit_mkt_data_with_protocol_2(data, version=version)
    
    # Parse the entire stream
    parser = Protocol2Parser([
        'bid', 'bid_size', 'ask', 'ask_size',
        'last', 'last_size', 'timestamp', 'transmission_time'
    ])
    
    results = parser.multi_parse(stream)
    
    # Verify we got all packets
    assert len(results) == 4
    
    # Verify versions are correct
    assert results[0]['_p2_version'] == 1
    assert results[1]['_p2_version'] == 1
    assert results[2]['_p2_version'] == 2
    assert results[3]['_p2_version'] == 2
    
    # Verify data integrity
    assert results[0]['symbol'] == 'BTC'
    assert results[1]['symbol'] == 'ETH'
    assert results[2]['symbol'] == 'SOL'
    assert results[3]['symbol'] == 'ADA'
    
    print(f"✓ Mixed version stream parsed correctly: 4 packets (2×v1, 2×v2)")


def test_version_field_not_in_data():
    """
    Verify that the version field is truly separate from the CSV data
    and doesn't interfere with field parsing.
    """
    print("\n=== Test Version Field Separation ===")
    
    mkt_data = CapitalComMKTDataLive(
        symbol='TEST',
        bid=100.0,
        bid_size=1.0,
        ask=101.0,
        ask_size=1.0,
        last=100.5,
        last_size=1.0,
        timestamp=1234567890
    )
    
    # Get the CSV data directly
    csv_data = mkt_data.transferable_2()
    
    # Verify version field is NOT in the CSV data
    assert b'|V=' not in csv_data
    assert b'V=1' not in csv_data
    assert b'V=2' not in csv_data
    
    # Now create packets with different versions
    packet_v1 = transmit_mkt_data_with_protocol_2(mkt_data, version=1)
    packet_v2 = transmit_mkt_data_with_protocol_2(mkt_data, version=2)
    
    # V1 should NOT have version field
    assert b'|V=' not in packet_v1
    
    # V2 SHOULD have version field
    assert b'|V=2' in packet_v2
    
    print("✓ Version field is correctly separated from CSV data")


def test_parser_ignores_unknown_fields():
    """
    Test that the parser correctly handles the expected field count
    regardless of version field presence.
    """
    print("\n=== Test Parser Field Count Validation ===")
    
    mkt_data = CapitalComMKTDataLive(
        symbol='XYZ',
        bid=50.0,
        bid_size=10.0,
        ask=51.0,
        ask_size=10.0,
        last=50.5,
        last_size=5.0,
        timestamp=int(time.time())
    )
    
    parser = Protocol2Parser([
        'bid', 'bid_size', 'ask', 'ask_size',
        'last', 'last_size', 'timestamp', 'transmission_time'
    ])
    
    # Both versions should parse correctly
    packet_v1 = transmit_mkt_data_with_protocol_2(mkt_data, version=1)
    packet_v2 = transmit_mkt_data_with_protocol_2(mkt_data, version=2)
    
    result_v1 = parser.parse(packet_v1)
    result_v2 = parser.parse(packet_v2)
    
    # Both should have exactly the expected number of data fields
    # (excluding symbol and _p2_version)
    assert len([k for k in result_v1.keys() if k not in ['symbol', '_p2_version']]) == 8
    assert len([k for k in result_v2.keys() if k not in ['symbol', '_p2_version']]) == 8
    
    print("✓ Parser correctly validates field count with and without version")


def run_all_tests():
    """Run all backward compatibility tests"""
    print("=" * 70)
    print("Protocol 2 Backward Compatibility Tests")
    print("=" * 70)
    
    tests = [
        test_old_parser_with_new_packets,
        test_new_parser_with_old_packets,
        test_ibkr_extended_class,
        test_stream_simulation,
        test_version_field_not_in_data,
        test_parser_ignores_unknown_fields,
    ]
    
    passed = 0
    failed = 0
    
    for test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n✗ {test_func.__name__} FAILED: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 70)
    print(f"Backward Compatibility Test Results: {passed} passed, {failed} failed")
    print("=" * 70)
    
    return failed == 0


if __name__ == '__main__':
    success = run_all_tests()
    exit(0 if success else 1)
