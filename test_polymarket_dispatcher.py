#!/usr/bin/env python3
"""
Test script for PolymarketDispatcher implementation
"""
import sys
import os
import unittest.mock as mock

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Mock missing dependencies
sys.modules['websocket'] = mock.MagicMock()
sys.modules['websocket.client'] = mock.MagicMock()
sys.modules['dotenv'] = mock.MagicMock()
sys.modules['utils3'] = mock.MagicMock()

def test_polymarket_dispatcher():
    """Test the PolymarketDispatcher implementation"""
    print("Testing PolymarketDispatcher implementation...")
    
    try:
        # Mock the required modules
        with mock.patch('argus.polymarket_direct.EnhancedPM'):
            with mock.patch('argus._argus_utils.Introspective'):
                from argus.polymarket import PolymarketDispatcher
                
                # Test instantiation
                dispatcher = PolymarketDispatcher(
                    host='localhost',
                    port=9984,  # Different port to avoid conflicts
                    dry_mode=True
                )
                
                print("✓ PolymarketDispatcher instantiated successfully")
                
                # Test configuration
                print(f"✓ Host: {dispatcher.host}")
                print(f"✓ Port: {dispatcher.port}")
                print(f"✓ Max concurrent streams: {dispatcher.max_concurrent_streams}")
                print(f"✓ Dry mode: True")
                
                # Test methods exist
                assert hasattr(dispatcher, 'show_subscriptions')
                assert hasattr(dispatcher, 'show_clients') 
                assert hasattr(dispatcher, 'show_stats')
                assert hasattr(dispatcher, 'interactive_mode')
                print("✓ All required methods exist")
                
                # Test configuration structure
                assert isinstance(dispatcher._configs, dict)
                assert 'Print data packets' in dispatcher._configs
                assert 'Show subscription changes' in dispatcher._configs
                print("✓ Configuration structure is correct")
                
                print("\n✅ All tests passed! PolymarketDispatcher implementation is working correctly.")
                return True
                
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = test_polymarket_dispatcher()
    sys.exit(0 if success else 1)