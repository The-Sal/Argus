/*
Implement a cache system for storing API responses.
This will be slighly different from the Python implementation.
*/


import Foundation

let fm = FileManager.default

func withLock(_ lock: NSLock, _ closure: () -> Void){
    lock.lock()
    closure()
    lock.unlock()
}

/// When an item is cached and before it is stored, the items .convertToData() method is called.
/// When the cache is being loaded the .convertFromData() method is called.
protocol CacheableItem{
    associatedtype DataType
    var id: UUID { get }
    func convertToData() throws -> Data
    func convertFromData(_ data: Data) throws -> DataType
}

typealias DiskCache = [String: [String: Data]]
typealias MemoryCache = [String: [String: any CacheableItem]]

extension MemoryCache{
    func convertToDiskCache() throws -> DiskCache {
        var diskCache: DiskCache = [:]
        for (domain, items) in self {
            var domainCache: [String: Data] = [:]
            for (key, item) in items {
                domainCache[key] = try item.convertToData()
            }
            diskCache[domain] = domainCache
        }
        return diskCache
    }
}

extension DiskCache{
    func toJSONData() throws -> Data {
        let jsonData = try JSONSerialization.data(withJSONObject: self, options: .prettyPrinted)
        return jsonData
    }

    func fromJSONData(_ data: Data) throws -> DiskCache {
        let json = try JSONSerialization.jsonObject(with: data, options: [])
        guard let diskCache = json as? DiskCache else {
            throw CacheError.coercionFailed("Failed to convert JSON data to DiskCache")
        }
        return diskCache
    }

}


enum CacheError: Error{
    case itemNotFound(String)
    case coercionFailed(String)
}

class Cache{
    static let shared = Cache()
    private let nslock = NSLock()

    private var items: MemoryCache = [:]
    private let cacheDiskPath: URL

    init(){
        let cachePath = fm.homeDirectoryForCurrentUser.appending(path: "/.argus/argus_server_cache.json")
        self.cacheDiskPath = cachePath
        print("Cache initialized at \(cacheDiskPath)")
    }

    func getItem(_ domain: String, _ key: String) throws -> (any CacheableItem)? {
        guard items.contains(where: { $0.key == domain}) else {
            throw CacheError.itemNotFound("Domain not found")
        }

        guard let item = items[domain]?[key] else {
            throw CacheError.itemNotFound("Item not found for key \(key) in domain \(domain)")
        }

        return item
    }

    func setItem(_ domain: String, _ key: String, _ item: any CacheableItem, _ secondsTillExpire: TimeInterval = -1) {
        self.nslock.lock()
        guard items.contains(where: { $0.key == domain}) else {
           items[domain] = [:]
           return
        }

        // this should be impossible to fail? given guard
        items[domain]![key] = item

        if secondsTillExpire > 0{
            let expireDate = Date(timeIntervalSinceNow: secondsTillExpire)
            items[domain]![convertKeyToTTLKey(key)] = expireDate
        }
        self.nslock.unlock()
    }

    func convertKeyToTTLKey(_ key: String) -> String{
        return "\(key)_ttl"
    }

    func saveCacheToDisk(){
        withLock(self.nslock, {
            do {
                let diskCache = try self.items.convertToDiskCache().toJSONData()
                try diskCache.write(to: self.cacheDiskPath)
            } catch {
                print("Error saving cache to disk: \(error)")
            }
        })
    }

}

class CacheManager{
    let cache = Cache.shared
    var domain: String

    init(_ domain: String){
        self.domain = domain
    }

    func cacheFunction<T: CacheableItem>(_ key: String, _ function: @escaping () -> T) throws -> T{
        let rawValue = try cache.getItem(self.domain, key)
        if let cachedValue = rawValue as? T{
            return cachedValue
        }else{
            print("Warning: Cache exists but the casting failed (Raw=\(rawValue as Any))->(Target=\(T.self))")
        }

        let value = function()
        cache.setItem(self.domain, key, value)
        return value
    }

}
