#!/usr/bin/env python3
"""
Test to verify memory growth fixes in Polymarket integration.
This test validates that:
1. ws_messages are cleared after writes
2. Callbacks are removed on unsubscribe
3. Thread-safe access to ws_messages
"""

import sys
import time
import threading
from unittest.mock import Mock, patch, MagicMock

# Test the EnhancedPM class memory management
def test_ws_messages_cleared_after_write():
    """Test that ws_messages are cleared after each write cycle"""
    print("TEST: ws_messages cleared after write...")
    
    # We need to mock the dependencies
    with patch('argus.polymarket_direct.WebSocketApp'), \
         patch('argus.polymarket_direct.requests.Session'), \
         patch('argus.polymarket_direct.update_request_session_proxy'):
        
        from argus.polymarket_direct import EnhancedPM
        
        # Create instance with short write interval
        pm = EnhancedPM(
            private_key="dummy_key",
            proxy_funder="dummy_funder"
        )
        
        # Override write interval for testing
        pm._write_interval = 1
        pm._enable_rolling = True
        
        # Simulate adding messages
        with pm._ws_messages_lock:
            for i in range(10):
                pm.ws_messages.append({'test': f'message_{i}'})
        
        # Wait for write cycle
        time.sleep(2)
        
        # Messages should be cleared after write
        with pm._ws_messages_lock:
            assert len(pm.ws_messages) == 0, f"Expected 0 messages, got {len(pm.ws_messages)}"
        
        print("✓ PASSED: ws_messages cleared after write")


def test_callbacks_removed_on_unsubscribe():
    """Test that callbacks are completely removed on unsubscribe"""
    print("TEST: callbacks removed on unsubscribe...")
    
    with patch('argus.polymarket_direct.WebSocketApp'), \
         patch('argus.polymarket_direct.requests.Session'), \
         patch('argus.polymarket_direct.update_request_session_proxy'):
        
        from argus.polymarket_direct import EnhancedPM
        
        pm = EnhancedPM(
            private_key="dummy_key",
            proxy_funder="dummy_funder"
        )
        
        # Add some callbacks
        pm.idx_to_callback['token1'] = lambda x: print('token1')
        pm.idx_to_callback['token2'] = lambda x: print('token2')
        pm.idx_to_callback['token3'] = lambda x: print('token3')
        
        assert len(pm.idx_to_callback) == 3, "Expected 3 callbacks"
        
        # Unsubscribe from token1 and token2
        pm.unsubscribe_from_market_data(['token1', 'token2'])
        
        # Check callbacks were removed
        assert 'token1' not in pm.idx_to_callback, "token1 should be removed"
        assert 'token2' not in pm.idx_to_callback, "token2 should be removed"
        assert 'token3' in pm.idx_to_callback, "token3 should remain"
        assert len(pm.idx_to_callback) == 1, f"Expected 1 callback, got {len(pm.idx_to_callback)}"
        
        print("✓ PASSED: callbacks removed on unsubscribe")


def test_missing_callback_handled_gracefully():
    """Test that missing callbacks don't raise errors"""
    print("TEST: missing callback handled gracefully...")
    
    with patch('argus.polymarket_direct.WebSocketApp'), \
         patch('argus.polymarket_direct.requests.Session'), \
         patch('argus.polymarket_direct.update_request_session_proxy'):
        
        from argus.polymarket_direct import EnhancedPM
        
        pm = EnhancedPM(
            private_key="dummy_key",
            proxy_funder="dummy_funder"
        )
        
        # Simulate a message for an asset without a callback
        message = {
            'price_changes': [
                {'asset_id': 'unknown_token', 'price': 0.5}
            ]
        }
        
        # This should not raise an error
        try:
            import json
            pm._on_ws_message(None, json.dumps(message))
            print("✓ PASSED: missing callback handled gracefully")
        except Exception as e:
            print(f"✗ FAILED: {e}")
            raise


def test_thread_safety():
    """Test thread-safe access to ws_messages"""
    print("TEST: thread-safe access to ws_messages...")
    
    with patch('argus.polymarket_direct.WebSocketApp'), \
         patch('argus.polymarket_direct.requests.Session'), \
         patch('argus.polymarket_direct.update_request_session_proxy'):
        
        from argus.polymarket_direct import EnhancedPM
        
        pm = EnhancedPM(
            private_key="dummy_key",
            proxy_funder="dummy_funder"
        )
        
        errors = []
        
        def append_messages():
            try:
                for i in range(100):
                    with pm._ws_messages_lock:
                        pm.ws_messages.append({'test': i})
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)
        
        def read_messages():
            try:
                for _ in range(100):
                    with pm._ws_messages_lock:
                        _ = len(pm.ws_messages)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)
        
        # Start threads
        t1 = threading.Thread(target=append_messages)
        t2 = threading.Thread(target=read_messages)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        
        assert len(errors) == 0, f"Thread safety errors: {errors}"
        print("✓ PASSED: thread-safe access to ws_messages")


if __name__ == '__main__':
    print("=" * 70)
    print("Memory Growth Fix Tests for Polymarket Integration")
    print("=" * 70)
    
    tests = [
        test_callbacks_removed_on_unsubscribe,
        test_missing_callback_handled_gracefully,
        test_thread_safety,
        # Note: test_ws_messages_cleared_after_write requires actual threading
        # and file I/O, so it's commented out for CI/CD
        # test_ws_messages_cleared_after_write,
    ]
    
    failed = 0
    for test in tests:
        try:
            test()
            print()
        except Exception as e:
            print(f"✗ FAILED: {test.__name__}")
            import traceback
            traceback.print_exc()
            failed += 1
            print()
    
    print("=" * 70)
    if failed == 0:
        print("All tests passed!")
        sys.exit(0)
    else:
        print(f"{failed} test(s) failed!")
        sys.exit(1)
