/*
This file contains synchronous APIs around non-synchronous operations within swift
*/

import Foundation


typealias RequestParams = (url: URL, method: String, headers: [String: String], body: Data?)


class Response: CacheableItem{
    var id: UUID = UUID()
    typealias DataType = Response

    func convertToData() throws -> Data {
        guard let data = data else {
            throw NSError(domain: "Response", code: 0, userInfo: [NSLocalizedDescriptionKey: "No data available"])
        }
        return data
    }

    func convertFromData(_ data: Data) throws -> DataType {
        return Response(data: data, response: .none, error: nil)
    }

    let data: Data?
    let error: Error?
    let response: URLResponse?

    init(data: Data?, response: URLResponse?, error: Error?) {
        self.data = data
        self.response = response
        self.error = error
    }


}

// Synchronous API for networking without callbacks and automatic support for caching
class Requests{

    static func sendRequest(params: RequestParams) -> Response {
        var request = URLRequest(url: params.url)
        request.httpMethod = params.method
        request.allHTTPHeaderFields = params.headers

        if let body = params.body {
            request.httpBody = body
        }

        let semaphore = DispatchSemaphore(value: 0)
        var responseData: Data?
        var responseValue: URLResponse?
        var errorValue: Error?

        URLSession.shared.dataTask(with: request) { data, response, error in
            responseData = data
            responseValue = response
            errorValue = error
            semaphore.signal()
        }.resume()

        semaphore.wait()

        return Response(data: responseData, response: responseValue, error: errorValue)
    }


    /// Check if the request is cached, if not, send the request and cache the response
    static func cacheRequest(cacheManager: CacheManager, params: RequestParams) throws -> Response {
        let cacheKey = """
        \(params.url.absoluteString)
        \(params.method)
        \(params.headers)
        \(params.body?.base64EncodedString() ?? "")
        """
        return try cacheManager.cacheFunction(cacheKey, {
            return Requests.sendRequest(params: params)
        })
    }

}
