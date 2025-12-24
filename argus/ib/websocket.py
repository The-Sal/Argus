# WebSocket stub for testing environments
# Provides dummy implementations to avoid dependency issues during development and testing.

class WebSocket:
    def __init__(self, url):
        self.url = url
        
    def connect(self):
        pass
        
    def send(self, message):
        pass
        
    def receive(self):
        return None
        
    def close(self):
        pass