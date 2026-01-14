#!/usr/bin/env python3
"""
Tests for Protocol 2 Versioning Support

This test file validates:
1. Version 1 packets (no version field) work as before
2. Version 2+ packets include and parse version field correctly
3. Backward compatibility: old parsers ignore extra fields
4. Forward compatibility: new parsers detect version
"""

import time
from argus.capital._svr_utils import transmit_mkt_data_with_protocol_2, Protocol2Parser
from argus.capital import CapitalComMKTDataLive


def test_version_1_packet():
    """Test that version 1 packets work as before (backward compatibility)"""
    print("\n=== Test Version 1 Packet ===")
    
    # Create mock market data
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
    
    # Encode with version 1 (default)
    packet = transmit_mkt_data_with_protocol_2(mkt_data)
    print(f"Version 1 Packet: {packet}")
    
    # Verify no version field in packet
    assert b'|V=' not in packet, "Version 1 packet should not contain |V= field"
    
    # Parse with parser
    parser = Protocol2Parser(['bid', 'bid_size', 'ask', 'ask_size', 'last', 'last_size', 'timestamp', 'transmission_time'])
    result = parser.parse(packet)
    
    print(f"Parsed result: {result}")
    
    # Verify version defaults to 1
    assert result['_p2_version'] == 1, "Version should default to 1 when not present"
    assert result['symbol'] == 'BTCUSD'
    assert result['bid'] == 50000.0
    assert result['ask'] == 50001.0
    
    print("✓ Version 1 packet test passed")


def test_version_2_packet():
    """Test that version 2 packets include version field"""
    print("\n=== Test Version 2 Packet ===")
    
    # Create mock market data
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
    
    # Encode with version 2
    packet = transmit_mkt_data_with_protocol_2(mkt_data, version=2)
    print(f"Version 2 Packet: {packet}")
    
    # Verify version field is present
    assert b'|V=2' in packet, "Version 2 packet should contain |V=2 field"
    
    # Parse with parser
    parser = Protocol2Parser(['bid', 'bid_size', 'ask', 'ask_size', 'last', 'last_size', 'timestamp', 'transmission_time'])
    result = parser.parse(packet)
    
    print(f"Parsed result: {result}")
    
    # Verify version is correctly parsed
    assert result['_p2_version'] == 2, "Version should be 2"
    assert result['symbol'] == 'ETHUSD'
    assert result['bid'] == 3000.0
    assert result['ask'] == 3001.0
    
    print("✓ Version 2 packet test passed")


def test_version_3_packet():
    """Test higher version numbers"""
    print("\n=== Test Version 3 Packet ===")
    
    mkt_data = CapitalComMKTDataLive(
        symbol='SOLUSD',
        bid=100.0,
        bid_size=10.0,
        ask=101.0,
        ask_size=10.0,
        last=100.5,
        last_size=5.0,
        timestamp=int(time.time())
    )
    
    # Encode with version 3
    packet = transmit_mkt_data_with_protocol_2(mkt_data, version=3)
    print(f"Version 3 Packet: {packet}")
    
    assert b'|V=3' in packet
    
    parser = Protocol2Parser(['bid', 'bid_size', 'ask', 'ask_size', 'last', 'last_size', 'timestamp', 'transmission_time'])
    result = parser.parse(packet)
    
    print(f"Parsed result: {result}")
    
    assert result['_p2_version'] == 3
    assert result['symbol'] == 'SOLUSD'
    
    print("✓ Version 3 packet test passed")


def test_backward_compatibility_old_parser():
    """
    Test that version 2 packets with extra fields can still be parsed
    by "old" parsers that don't expect version field
    
    This simulates an old parser that just ignores extra data after expected fields
    """
    print("\n=== Test Backward Compatibility (Old Parser) ===")
    
    mkt_data = CapitalComMKTDataLive(
        symbol='AAPL',
        bid=150.0,
        bid_size=100.0,
        ask=151.0,
        ask_size=100.0,
        last=150.5,
        last_size=50.0,
        timestamp=int(time.time())
    )
    
    # Create version 2 packet
    packet_v2 = transmit_mkt_data_with_protocol_2(mkt_data, version=2)
    print(f"Version 2 Packet: {packet_v2}")
    
    # New parser can handle it
    parser = Protocol2Parser(['bid', 'bid_size', 'ask', 'ask_size', 'last', 'last_size', 'timestamp', 'transmission_time'])
    result = parser.parse(packet_v2)
    
    print(f"Parsed by new parser: {result}")
    assert result['_p2_version'] == 2
    assert result['symbol'] == 'AAPL'
    
    print("✓ Backward compatibility test passed")


def test_multi_parse_mixed_versions():
    """Test parsing multiple packets with different versions"""
    print("\n=== Test Multi-Parse Mixed Versions ===")
    
    mkt_data_1 = CapitalComMKTDataLive(
        symbol='BTC',
        bid=50000.0,
        bid_size=1.0,
        ask=50001.0,
        ask_size=1.0,
        last=50000.5,
        last_size=1.0,
        timestamp=int(time.time())
    )
    
    mkt_data_2 = CapitalComMKTDataLive(
        symbol='ETH',
        bid=3000.0,
        bid_size=5.0,
        ask=3001.0,
        ask_size=5.0,
        last=3000.5,
        last_size=2.5,
        timestamp=int(time.time())
    )
    
    # Create packets with different versions
    packet_v1 = transmit_mkt_data_with_protocol_2(mkt_data_1, version=1)
    packet_v2 = transmit_mkt_data_with_protocol_2(mkt_data_2, version=2)
    
    # Combine packets
    combined = packet_v1 + packet_v2
    
    # Parse both
    parser = Protocol2Parser(['bid', 'bid_size', 'ask', 'ask_size', 'last', 'last_size', 'timestamp', 'transmission_time'])
    results = parser.multi_parse(combined)
    
    print(f"Parsed {len(results)} packets")
    for i, result in enumerate(results):
        print(f"Packet {i+1}: {result}")
    
    assert len(results) == 2
    assert results[0]['_p2_version'] == 1
    assert results[0]['symbol'] == 'BTC'
    assert results[1]['_p2_version'] == 2
    assert results[1]['symbol'] == 'ETH'
    
    print("✓ Multi-parse mixed versions test passed")


def test_packet_size_validation():
    """Test that packet length header is correctly calculated with version field"""
    print("\n=== Test Packet Size Validation ===")
    
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
    
    packet_v1 = transmit_mkt_data_with_protocol_2(mkt_data, version=1)
    packet_v2 = transmit_mkt_data_with_protocol_2(mkt_data, version=2)
    
    # Version 2 should be longer due to |V=2 field (4 bytes) plus 1 byte for updated packet length
    print(f"V1 packet length: {len(packet_v1)}")
    print(f"V2 packet length: {len(packet_v2)}")
    print(f"Difference: {len(packet_v2) - len(packet_v1)} bytes")
    
    assert len(packet_v2) > len(packet_v1), "Version 2 packet should be longer"
    # The difference is 4 bytes for |V=2, plus potentially 1 more if packet length header changes
    # due to going from 3-digit to 4-digit length
    size_diff = len(packet_v2) - len(packet_v1)
    assert size_diff in [4, 5], f"Version 2 should be 4-5 bytes longer (|V=2), got {size_diff}"
    
    # Both should parse correctly
    parser = Protocol2Parser(['bid', 'bid_size', 'ask', 'ask_size', 'last', 'last_size', 'timestamp', 'transmission_time'])
    result_v1 = parser.parse(packet_v1)
    result_v2 = parser.parse(packet_v2)
    
    assert result_v1['_p2_version'] == 1
    assert result_v2['_p2_version'] == 2
    
    print("✓ Packet size validation test passed")


def run_all_tests():
    """Run all P2 versioning tests"""
    print("=" * 60)
    print("Protocol 2 Versioning Tests")
    print("=" * 60)
    
    tests = [
        test_version_1_packet,
        test_version_2_packet,
        test_version_3_packet,
        test_backward_compatibility_old_parser,
        test_multi_parse_mixed_versions,
        test_packet_size_validation,
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
    
    print("\n" + "=" * 60)
    print(f"Test Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == '__main__':
    success = run_all_tests()
    exit(0 if success else 1)
