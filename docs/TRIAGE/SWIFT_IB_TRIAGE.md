# Swift IB Architectural Patterns: Porting Python's Extensible Design

## Overview

This document explains **how** Python's IB implementation achieves extensibility and **how to port** these architectural patterns to Swift. The focus is on understanding the design patterns rather than enumerating features.

**Current Gap:** Swift IB has hardcoded implementations where Python uses extensible patterns.

**Goal:** Port Python's architectural patterns to create a Swift implementation that's equally extensible.

---

## Architecture Pattern 1: Introspective Framework

### Python's Design

Python uses the **`Introspective` base class** to create a meta-programming layer for runtime extensibility.

**Location:** `argus/_argus_utils.py`

**Key Pattern:**

```python
class Introspective:
    """Enables classes to expose methods for interactive runtime access."""
    
    def _interactive_ui(self, functions: dict):
        """
        Generic interactive menu system.
        
        Args:
            functions: dict mapping 'name' -> ('description', callable)
        """
        # Automatically adds 'call_method' to discover class methods
        functions['call_method'] = ('Interactively call a method of this class', self.call_method)
        functions['exit'] = ('Exit the interactive UI', lambda: None)
        
        while True:
            # Display all registered functions
            for i, (name, (doc, _)) in enumerate(functions.items(), 1):
                print(f"{i}. {name} - {doc}")
            
            # User selects and invokes
            choice = int(input("Choose: "))
            func_name = list(functions.keys())[choice - 1]
            func = functions[func_name][1]
            func()
    
    def call_method(self):
        """Uses Python's inspect module to discover all public methods."""
        methods = {name: func for name, func in inspect.getmembers(self, predicate=inspect.ismethod)
                   if not name.startswith('_')}
        # Let user select and invoke ANY public method
```

**How it's used in Binance:**

```python
class BinanceMKTDispatcher(Introspective):
    def interactive_mode(self):
        functions = {
            'show_subscriptions': ('Show all active symbol subscriptions', self.show_subscriptions),
            'show_clients': ('Show all connected clients', self.show_clients),
            'modify_configs': ('Modify dispatcher configurations', self._modify_configs_interactive),
        }
        self._interactive_ui(functions)  # Delegates to base class
```

**Why It's Extensible:**
1. **New features are just dictionary entries** - no menu rewrite needed
2. **call_method provides runtime introspection** - can call ANY public method
3. **No hardcoded command parsing** - framework handles dispatch

### Swift Port Strategy

**Pattern: Protocol + Dictionary-Based Dispatch**

```swift
// 1. Define protocol for interactive functions
protocol InteractiveFunction {
    var name: String { get }
    var description: String { get }
    func execute()
}

// 2. Concrete implementation wraps closures
struct InteractiveClosure: InteractiveFunction {
    let name: String
    let description: String
    private let closure: () -> Void
    
    func execute() {
        closure()
    }
}

// 3. Base protocol for Introspective behavior
protocol Introspective: AnyObject {
    var interactiveFunctions: [String: InteractiveFunction] { get set }
    func registerFunction(name: String, description: String, function: @escaping () -> Void)
    func interactiveMode()
}

// 4. Default implementation via protocol extension
extension Introspective {
    func registerFunction(name: String, description: String, function: @escaping () -> Void) {
        interactiveFunctions[name] = InteractiveClosure(
            name: name,
            description: description,
            closure: function
        )
    }
    
    func interactiveMode() {
        // Add meta-function to call any method by name
        registerFunction(
            name: "call_method",
            description: "Call any public method by name",
            function: callMethodInteractive
        )
        
        while true {
            print("\nAvailable functions:")
            let sorted = interactiveFunctions.keys.sorted()
            for (i, key) in sorted.enumerated() {
                let func = interactiveFunctions[key]!
                print("\(i + 1). \(key) - \(func.description)")
            }
            
            print("0. Exit")
            print("\nSelect: ", terminator: "")
            
            guard let input = readLine(),
                  let choice = Int(input) else { continue }
            
            if choice == 0 { break }
            
            let key = sorted[choice - 1]
            interactiveFunctions[key]?.execute()
        }
    }
    
    func callMethodInteractive() {
        // Use Swift's Mirror API for runtime introspection
        let mirror = Mirror(reflecting: self)
        print("\nInspecting instance of \(mirror.subjectType)")
        
        // Note: Swift doesn't have Python's inspect module
        // This is a simplified version - full implementation would need
        // method signature discovery via objc runtime or code generation
        print("(Swift limitation: Runtime method discovery requires @objc or code generation)")
    }
}
```

**Using the Pattern:**

```swift
class IBMKTDispatcher: Introspective {
    var interactiveFunctions: [String: InteractiveFunction] = [:]
    
    func setupInteractive() {
        // Register functions - easily extensible
        registerFunction(
            name: "show_contracts",
            description: "Show subscribed contracts",
            function: showSubscribedContracts
        )
        
        registerFunction(
            name: "show_clients",
            description: "Show connected clients",
            function: showConnectedClients
        )
        
        registerFunction(
            name: "show_configs",
            description: "Show current configurations",
            function: showConfigurations
        )
        
        // Add more easily - no menu rewrite needed
        registerFunction(
            name: "search",
            description: "Search for contract",
            function: searchContractInteractive
        )
    }
}
```

**Key Architectural Benefits:**
1. **New commands = one line** - `registerFunction(...)` anywhere
2. **No switch/case explosion** - dictionary dispatch
3. **Can register at runtime** - dynamic extension
4. **Testable** - can verify registered functions
5. **Composable** - subclasses inherit parent's functions

**Python vs Swift Trade-offs:**

| Aspect | Python | Swift |
|:-------|:-------|:------|
| Runtime introspection | `inspect.getmembers()` | Limited (Mirror API read-only, or @objc runtime) |
| Method discovery | Automatic | Requires registration or code generation |
| Type safety | Runtime | Compile-time |
| Extensibility | Fully dynamic | Protocol-based, compile-time safe |

**Recommendation:** Accept Swift's registration requirement but gain type safety and performance. The pattern is still highly extensible.

---

## Architecture Pattern 2: Domain-Based Caching System

### Python's Design

Python uses **`DomainCache`** - a namespace-based persistent cache with decorator support.

**Location:** `argus/cache_utils/__init__.py`

**Key Pattern:**

```python
# Conceptual structure
class DomainCache:
    """
    Cache organized by 'domains' (namespaces).
    Each module has its own domain to avoid collisions.
    """
    def __init__(self, cache_file='~/.argus/capital_cache.pkl'):
        self.cache = {}  # domain -> key -> value
        self.cache_file = cache_file
        self._load_from_disk()
    
    def cache_decorator(self, domain: str):
        """Decorator that automatically caches function results."""
        def decorator(func):
            def wrapper(*args, **kwargs):
                # Generate cache key from function name + args
                cache_key = f"{func.__name__}:{args}:{kwargs}"
                
                # Check cache
                if cache_key in self.cache.get(domain, {}):
                    return self.cache[domain][cache_key]
                
                # Execute and cache
                result = func(*args, **kwargs)
                self.cache.setdefault(domain, {})[cache_key] = result
                self._save_to_disk()
                return result
            return wrapper
        return decorator
```

**How it's used in IB:**

```python
_IB_Cache = DomainCache('~/.argus/ib_cache.pkl')

class IBNetworker:
    @_IB_Cache.cache_decorator('IBNetworker.search_contract')
    def search_contract(self, contract_name):
        # This automatically caches results to disk
        # On restart, cached results load instantly
        return expensive_api_call(contract_name)
```

**Why It's Powerful:**
1. **Decorator-based** - transparent caching, no manual cache checks
2. **Domain isolation** - different modules don't collide
3. **Persistent** - survives restarts
4. **Centralized** - one cache file, easy to inspect/clear

### Swift Port Strategy

**Pattern: Protocol + Generic Cache Manager + Property Wrappers**

```swift
// 1. Protocol for cacheable values
protocol Cacheable: Codable {
    // Codable ensures we can save to JSON/disk
}

// 2. Domain-based cache manager
class DomainCache {
    private let domain: String
    private let cacheDirectory: URL
    private var cache: [String: Data] = [:]
    private let lock = NSLock()
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()
    
    init(domain: String) {
        self.domain = domain
        
        // Use ~/.argus/ like Python
        let home = FileManager.default.homeDirectoryForCurrentUser
        self.cacheDirectory = home.appendingPathComponent(".argus")
        
        // Create directory if needed
        try? FileManager.default.createDirectory(
            at: cacheDirectory,
            withIntermediateDirectories: true
        )
        
        loadFromDisk()
    }
    
    private func cacheFilePath() -> URL {
        return cacheDirectory.appendingPathComponent("\(domain)_cache.json")
    }
    
    private func loadFromDisk() {
        lock.lock()
        defer { lock.unlock() }
        
        let path = cacheFilePath()
        guard let data = try? Data(contentsOf: path),
              let decoded = try? decoder.decode([String: Data].self, from: data) else {
            print("[DomainCache:\(domain)] No existing cache or failed to load")
            return
        }
        
        cache = decoded
        print("[DomainCache:\(domain)] Loaded \(cache.count) entries")
    }
    
    func saveToDisk() {
        lock.lock()
        let cacheCopy = cache
        lock.unlock()
        
        // Save asynchronously to avoid blocking
        DispatchQueue.global(qos: .utility).async {
            do {
                let data = try self.encoder.encode(cacheCopy)
                try data.write(to: self.cacheFilePath())
                print("[DomainCache:\(self.domain)] Saved \(cacheCopy.count) entries")
            } catch {
                print("[DomainCache:\(self.domain)] Save failed: \(error)")
            }
        }
    }
    
    func get<T: Cacheable>(_ key: String) -> T? {
        lock.lock()
        defer { lock.unlock() }
        
        guard let data = cache[key],
              let value = try? decoder.decode(T.self, from: data) else {
            return nil
        }
        return value
    }
    
    func set<T: Cacheable>(_ key: String, value: T) {
        do {
            let data = try encoder.encode(value)
            
            lock.lock()
            cache[key] = data
            lock.unlock()
            
            saveToDisk()
        } catch {
            print("[DomainCache:\(domain)] Failed to encode \(key): \(error)")
        }
    }
    
    func clear() {
        lock.lock()
        cache.removeAll()
        lock.unlock()
        saveToDisk()
    }
}

// 3. Decorator equivalent using wrapper type
class CachedFunction<Input: Hashable, Output: Cacheable> {
    private let cache: DomainCache
    private let cacheKeyPrefix: String
    private let function: (Input) -> Output
    
    init(cache: DomainCache, name: String, function: @escaping (Input) -> Output) {
        self.cache = cache
        self.cacheKeyPrefix = name
        self.function = function
    }
    
    func callAsFunction(_ input: Input) -> Output {
        let key = "\(cacheKeyPrefix):\(input)"
        
        // Check cache
        if let cached: Output = cache.get(key) {
            print("[Cache HIT] \(cacheKeyPrefix)")
            return cached
        }
        
        // Execute and cache
        print("[Cache MISS] \(cacheKeyPrefix)")
        let result = function(input)
        cache.set(key, value: result)
        return result
    }
}
```

**Using the Pattern:**

```swift
// Make SearchResult cacheable
extension SearchResult: Cacheable {
    // Codable conformance handles serialization
}

class IBNetworker {
    private let cache = DomainCache(domain: "ib")
    
    // Cached version of expensive function
    private lazy var cachedSearchContract = CachedFunction(
        cache: cache,
        name: "searchContract"
    ) { (symbol: String) -> [SearchResult] in
        // This closure contains the expensive operation
        return self.performActualSearch(symbol: symbol)
    }
    
    func searchContract(symbol: String) -> [SearchResult] {
        // Automatically uses cache
        return cachedSearchContract(symbol)
    }
    
    private func performActualSearch(symbol: String) -> [SearchResult] {
        // Expensive IBKR API call
        // ...
    }
}
```

**Alternative: Property Wrapper for Simple Cases**

```swift
@propertyWrapper
struct Cached<T: Cacheable> {
    private let key: String
    private let cache: DomainCache
    
    var wrappedValue: T? {
        get { cache.get(key) }
        nonmutating set {
            if let value = newValue {
                cache.set(key, value: value)
            }
        }
    }
    
    init(key: String, cache: DomainCache) {
        self.key = key
        self.cache = cache
    }
}
```

**Key Architectural Benefits:**
1. **Persistent across restarts** - JSON on disk like Python's pickle
2. **Type-safe** - Codable ensures serializability
3. **Thread-safe** - NSLock protects concurrent access
4. **Transparent** - `CachedFunction` wrapper makes caching implicit
5. **Domain isolation** - each module has its cache file

**Python vs Swift Trade-offs:**

| Aspect | Python | Swift |
|:-------|:-------|:------|
| Decorator syntax | `@cache.decorator()` | Wrapper type or property wrapper |
| Storage format | Pickle (binary) | JSON (human-readable) |
| Type checking | Runtime | Compile-time (via Codable) |
| Introspection | Can cache anything | Must conform to Codable |

**Recommendation:** Use JSON instead of binary format for debuggability. Accept Codable requirement for type safety. The pattern achieves same extensibility.

---

## Architecture Pattern 3: Configuration System

### Python's Design

**Pattern: Dictionary with Interactive Modification**

```python
class MKTDispatcher:
    def __init__(self):
        self._configs = {
            'Print data packets': False,
            'Use TQDM Progress bar': False,
            'Block New MKT Data': True,
            # Easily add more
        }
    
    def _modify_configs_interactive(self):
        """Let user change any config at runtime."""
        for key, value in self._configs.items():
            print(f"{key}: {value}")
        
        choice = input("Configuration: ")
        if choice in self._configs:
            new_value = input(f"New value: ")
            # Auto-parse boolean
            if new_value.lower() == 'true':
                self._configs[choice] = True
            # ... etc
```

**Why It's Extensible:**
1. **New configs = new dict entries** - no code changes needed elsewhere
2. **Runtime modification** - no restart required
3. **Type-agnostic** - stores Any type

### Swift Port Strategy

**Pattern: Enum + Dictionary with Type-Safe Access**

```swift
// 1. Define configuration keys as enum for type safety
enum ConfigKey: String, CaseIterable {
    case printPackets = "Print data packets"
    case blockNewData = "Block New MKT Data"
    case showBlockedWarning = "Show blocked MKT Data Warning"
    // Adding new config = adding enum case
}

// 2. Configuration manager
class ConfigurationManager {
    private var configs: [ConfigKey: Any] = [:]
    private let lock = NSLock()
    
    subscript(key: ConfigKey) -> Any? {
        get {
            lock.lock()
            defer { lock.unlock() }
            return configs[key]
        }
        set {
            lock.lock()
            defer { lock.unlock() }
            configs[key] = newValue
        }
    }
    
    // Type-safe accessors
    func getBool(_ key: ConfigKey, default defaultValue: Bool = false) -> Bool {
        return (self[key] as? Bool) ?? defaultValue
    }
    
    func setBool(_ key: ConfigKey, value: Bool) {
        self[key] = value
    }
    
    // Interactive modification
    func modifyInteractive() {
        print("\n=== Configurations ===")
        for key in ConfigKey.allCases {
            print("\(key.rawValue): \(self[key] ?? "nil")")
        }
        
        print("\nSelect configuration (or 'done'):")
        for (i, key) in ConfigKey.allCases.enumerated() {
            print("\(i + 1). \(key.rawValue)")
        }
        
        guard let input = readLine(),
              let choice = Int(input),
              choice > 0,
              choice <= ConfigKey.allCases.count else {
            return
        }
        
        let key = Array(ConfigKey.allCases)[choice - 1]
        print("Enter new value for '\(key.rawValue)': ", terminator: "")
        
        guard let newValue = readLine()?.trimmingCharacters(in: .whitespacesAndNewlines) else {
            return
        }
        
        // Parse value based on current type
        if let currentBool = self[key] as? Bool {
            // It's a boolean config
            if newValue.lowercased() == "true" {
                self[key] = true
            } else if newValue.lowercased() == "false" {
                self[key] = false
            }
        } else {
            // String config
            self[key] = newValue
        }
        
        print("Updated '\(key.rawValue)' to '\(self[key] ?? "nil")'")
    }
}
```

**Using the Pattern:**

```swift
class IBMKTDispatcher {
    let config = ConfigurationManager()
    
    init() {
        // Set defaults
        config.setBool(.printPackets, value: false)
        config.setBool(.blockNewData, value: true)
        config.setBool(.showBlockedWarning, value: false)
    }
    
    func processMarketData(_ data: IBMarketData) {
        // Use configs
        if config.getBool(.printPackets) {
            print("Market data: \(data)")
        }
        
        if config.getBool(.blockNewData) {
            // Block logic
        }
    }
}
```

**Key Architectural Benefits:**
1. **Type-safe keys** - enum prevents typos
2. **Runtime modification** - no restart needed
3. **Extensible** - add enum case = add config
4. **Discoverable** - `ConfigKey.allCases` lists all
5. **Thread-safe** - NSLock protection

---

## Implementation Roadmap

### Phase 1: Port Introspective Pattern (3-4 days)

**Objective:** Replace hardcoded switch in `interactiveMode()` with protocol-based dispatch.

**Steps:**
1. Create `Introspective` protocol with default `interactiveMode()` implementation
2. Make `IBMKTDispatcher` conform to `Introspective`
3. Register all existing functions (show contracts, clients, etc.)
4. Test that all commands work via registration

**Success:** Can add new interactive commands by calling `registerFunction()` anywhere.

### Phase 2: Port DomainCache Pattern (3-4 days)

**Objective:** Replace in-memory caches with disk-persisted domain caches.

**Steps:**
1. Create `DomainCache` class with JSON persistence
2. Make `SearchResult` conform to `Codable`
3. Create `CachedFunction` wrapper for `searchContract`
4. Verify cache file created at `~/.argus/ib_cache.json`
5. Test restart - second search should be instant

**Success:** Contract searches cached to disk, fast startup on restart.

### Phase 3: Port Configuration Pattern (2-3 days)

**Objective:** Make configuration system extensible.

**Steps:**
1. Create `ConfigKey` enum with existing configs
2. Create `ConfigurationManager` class
3. Replace `configs` dictionary with manager
4. Add interactive modification command
5. Test runtime config changes take effect

**Success:** Can add new configs by adding enum case, modify at runtime.

---

## Key Architectural Principles

### 1. Protocol-Oriented Design
- **Python:** Uses duck typing and base classes
- **Swift:** Use protocols with default implementations
- **Benefit:** Compile-time safety + same extensibility

### 2. Dictionary-Based Dispatch
- **Python:** `functions[name]()`
- **Swift:** `interactiveFunctions[name]?.execute()`
- **Benefit:** No switch statement explosion

### 3. Type-Safe Persistence
- **Python:** Pickle (any object)
- **Swift:** Codable (type-safe serialization)
- **Benefit:** Human-readable JSON + compile-time checking

### 4. Registration Over Discovery
- **Python:** `inspect.getmembers()` finds methods automatically
- **Swift:** Explicit `registerFunction()` calls
- **Benefit:** Clear, explicit, still extensible

### 5. Enum-Based Configuration
- **Python:** String keys (typo-prone)
- **Swift:** Enum keys (typo-proof)
- **Benefit:** Compiler catches errors

---

## What's NOT Being Ported

### Non-Essential Dispatcher Modes

**ASK, ASK+BID+LAST, FULL_PKL, FULL_JSON modes are NOT priorities for initial implementation.**

**Rationale:**
- Only Protocol 2 is used in production systems
- Other modes exist for legacy client compatibility
- Swift can focus on Protocol 2 exclusively for MVP
- Simplifies codebase without reducing production value

**When to add them:**
- If specific clients require lightweight ASK mode for low-bandwidth scenarios
- If cross-language clients need FULL_JSON for interoperability
- If legacy Python clients need ASK+BID+LAST compatibility

If other modes become necessary, they can be added using the same pattern as Protocol 2 formatting—the extensible architecture makes this straightforward.

---

## Summary

The path to Swift IB parity is not about copying features—it's about **porting architectural patterns**:

1. **Introspective Pattern** → Protocol + Dictionary Dispatch
2. **DomainCache Pattern** → Codable + JSON Persistence  
3. **Configuration Pattern** → Enum + Type-Safe Manager

These patterns provide the **same extensibility** as Python while leveraging Swift's strengths:
- Type safety catches errors at compile time
- Protocols enable flexible composition
- Codable ensures serializability
- Enums prevent typos

Once these patterns are in place, adding new features becomes trivial—just like in Python.

**Estimated Effort:** 8-11 days for three patterns, creating a foundation as extensible as Python's.
