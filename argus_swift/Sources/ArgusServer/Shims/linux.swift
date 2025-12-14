#if canImport(FoundationNetworking)  // basically not macOS
/*
Patching all Foundation Networking -> Project Wide
This way you do not need to import `FoundationNetworking` like some brude
*/
import FoundationNetworking
public typealias URLSession = FoundationNetworking.URLSession
public typealias HTTPURLResponse = FoundationNetworking.HTTPURLResponse
public typealias URLSessionConfiguration = FoundationNetworking.URLSessionConfiguration
public typealias URLRequest = FoundationNetworking.URLRequest
public typealias URLSessionWebSocketTask = FoundationNetworking.URLSessionWebSocketTask


/*
Patching all Glibc functions under the alias 'Darwin' to
allow code that calls Darwin to run without hassle fixing conversion between types
*/
import Glibc
public enum Darwin {

    // TL;DR Glibc uses Int32 and for some unknown reason that only God and whoever had
    // this terrible idea–prolly same kinda person who comes up with Obj-C thought to have
    // this special __socket_type rather than just keep it all Int32
    public static func socket(_ domain: Int32, _ type: __socket_type, _ protocol: Int32) -> Int32 {
        return Glibc.socket(domain, Int32(type.rawValue), `protocol`)
    }

    public static func accept(_ socket: Int32, _ clientAddr: UnsafeMutablePointer<sockaddr>!, _ socketLen: UnsafeMutablePointer<socklen_t>!) -> Int32{
        return Glibc.accept(socket, clientAddr, socketLen)
    }
}

public func socket(_ domain: Int32, _ type: __socket_type, _ protocol: Int32) -> Int32{
    return Darwin.socket(domain, type, `protocol`)
}


#endif
