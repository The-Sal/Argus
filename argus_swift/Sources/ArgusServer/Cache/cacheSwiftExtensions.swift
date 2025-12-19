/*

In the cache.swift file we defined a protocol for CacheableItem, here are are
going to extend the primitive swift types to automatically work with this protocol

*/

import Foundation

enum CachePrimitiveConversionErrors: Error{
    case unableToConvertCacheToTarget(String) // This error is when loading from the cache
    case unableToConvertTargetToCache(String) // This error is when saving to the cache
}

extension String: CacheableItem{
    typealias DataType = String

    var id: UUID {
        UUID()
    }

    func convertToData() -> Data {
        return Data(self.utf8)
    }

    func convertFromData(_ data: Data) throws -> DataType {
        guard let string = String(data: data, encoding: .utf8) else {
            throw CachePrimitiveConversionErrors.unableToConvertCacheToTarget(
                "Unable to convert cache data (\(data)) to String"
            )
        }
        return string
    }
}

/// Pardon the String <-> Int conversion its just easier than converting it to/from data
extension Int: CacheableItem{
    typealias DataType = Int

    var id: UUID {
        UUID()
    }

    func convertToData() throws -> Data {
        guard let data = "\(self)".data(using: .utf8) else {
            throw CachePrimitiveConversionErrors.unableToConvertTargetToCache(
                "Unable to convert Int (\(self)) to Data"
            )
        }
        return data
    }

    func convertFromData(_ data: Data) throws -> DataType {
        guard let string = String(data: data, encoding: .utf8) else {
            throw CachePrimitiveConversionErrors.unableToConvertCacheToTarget(
                "Unable to convert cache data (\(data)) to String"
            )
        }

        guard let int = Int(string) else {
            throw CachePrimitiveConversionErrors.unableToConvertCacheToTarget(
                "Unable to convert String (\(string)) to Int"
            )
        }
        return int
    }
}

/// Pardon the String <-> Double conversion its just easier than converting it to/from data
extension Double: CacheableItem{
    typealias DataType = Double

    var id: UUID {
        UUID()
    }

    func convertToData() throws -> Data {
        guard let data = "\(self)".data(using: .utf8) else {
            throw CachePrimitiveConversionErrors.unableToConvertTargetToCache(
                "Unable to convert Double (\(self)) to Data"
            )
        }
        return data
    }

    func convertFromData(_ data: Data) throws -> DataType {
        guard let string = String(data: data, encoding: .utf8) else {
            throw CachePrimitiveConversionErrors.unableToConvertCacheToTarget(
                "Unable to convert cache data (\(data)) to String"
            )
        }

        guard let double = Double(string) else {
            throw CachePrimitiveConversionErrors.unableToConvertCacheToTarget(
                "Unable to convert String (\(string)) to Double"
            )
        }
        return double
    }
}


extension Float: CacheableItem{
    typealias DataType = Float

    var id: UUID {
        UUID()
    }

    func convertToData() throws -> Data {
        guard let data = "\(self)".data(using: .utf8) else {
            throw CachePrimitiveConversionErrors.unableToConvertTargetToCache(
                "Unable to convert Float (\(self)) to Data"
            )
        }
        return data
    }

    func convertFromData(_ data: Data) throws -> DataType {
        guard let string = String(data: data, encoding: .utf8) else {
            throw CachePrimitiveConversionErrors.unableToConvertCacheToTarget(
                "Unable to convert cache data (\(data)) to String"
            )
        }

        guard let float = Float(string) else {
            throw CachePrimitiveConversionErrors.unableToConvertCacheToTarget(
                "Unable to convert String (\(string)) to Float"
            )
        }
        return float
    }
}


extension Date: CacheableItem{
    typealias DataType = Date

    var id: UUID {
        UUID()
    }

    func convertToData() throws -> Data {
        let timeInterval = self.timeIntervalSince1970
        return "\(timeInterval)".data(using: .utf8)!
    }

    func convertFromData(_ data: Data) throws -> DataType {
        guard let string = String(data: data, encoding: .utf8) else {
            throw CachePrimitiveConversionErrors.unableToConvertCacheToTarget(
                "Unable to convert cache data (\(data)) to String"
            )
        }

        guard let timeInterval = TimeInterval(string) else {
            throw CachePrimitiveConversionErrors.unableToConvertCacheToTarget(
                "Unable to convert String (\(string)) to TimeInterval"
            )
        }
        return Date(timeIntervalSince1970: timeInterval)
    }
}
