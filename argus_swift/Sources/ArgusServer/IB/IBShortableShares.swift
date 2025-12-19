import Foundation

enum ShortableSharesError: Error {
    case invalidFormat(String)
}

struct ShortableShareEntry{
    /// Based on
    /*
    #SYM|CUR|NAME|CON|ISIN|REBATERATE|FEERATE|AVAILABLE|FIGI|

    A|USD|AGILENT TECHNOLOGIES INC|1715006|XXXXXXXU1016|3.2319|0.4081|4100000|BBG000C2V3D6|
    AA|USD|ALCOA CORP|251962528|XXXXXXX21065|3.3761|0.2639|4400000|BBG00B3T3HD3|
    AAA|USD|ALTERNATIVE ACCESS FIRST PRI|591368776|XXXXXXXX6105|-0.1984|3.8384|10000|BBG01B0JRCS6|
    */

    let symbol: String
    let currency: String
    let name: String
    let conid: Int
    let isin: String
    let rebateRate: Double
    let feeRate: Double
    let available: Double
    let figi: String

    init(symbol: String, currency: String, name: String, conid: Int, isin: String, rebateRate: Double, feeRate: Double, available: Double, figi: String) {
        self.symbol = symbol
        self.currency = currency
        self.name = name
        self.conid = conid
        self.isin = isin
        self.rebateRate = rebateRate
        self.feeRate = feeRate
        self.available = available
        self.figi = figi
    }

    init(_ fromString: String) throws {
        let components = fromString.split(separator: "|")
        guard components.count >= 8 else {
            throw ShortableSharesError.invalidFormat(
                "Invalid format for ShortableShares. Found \(components.count) components\nRaw=\(fromString)")
        }
        self.symbol = String(components[0])
        self.currency = String(components[1])
        self.name = String(components[2])
        self.conid = Int(components[3]) ?? 0
        self.isin = String(components[4])
        self.rebateRate = Double(components[5]) ?? 0.0
        self.feeRate = Double(components[6]) ?? 0.0
        self.available = Double(components[7]) ?? 0.0
        self.figi = String(components[8])
    }

}

struct ShortableShareFastDB{
    let entries: [ShortableShareEntry]
    let symbolToEntry: [String: Int]  // where Int is the index within self.entry
    let conidToEntry: [Int: Int]  // where Int is the index within self.entry
    init(entries: [ShortableShareEntry]) {
        self.entries = entries
        self.symbolToEntry = Dictionary(uniqueKeysWithValues: entries.enumerated().map { ($0.element.symbol, $0.offset) })
        self.conidToEntry = Dictionary(uniqueKeysWithValues: entries.enumerated().map { ($0.element.conid, $0.offset) })
    }
}

class ShortableSharesData {
    private var symbolToConidMap: [String: Int] = [:]
    private let lock = NSLock()

    // TODO: Make this dynaically set rather than fixed
    let ibkrFtp = "ftp://ftp2.interactivebrokers.com/usa.txt"
    let username = "shortstock"
    let password = ""
    let skipLinesOfFtp = 2 // based on the structure of the ftp .txt file

    var database: ShortableShareFastDB? = nil

    func downloadShortableShares() throws -> ShortableShareFastDB {
        print("Please wait while downloading shortable shares...")
        let rawContent = try HTTPClient().ftp(url: self.ibkrFtp, username: self.username, password: self.password)
        let rawData: [ShortableShareEntry] = rawContent.components(separatedBy: "\n").compactMap({
            do{
                return try ShortableShareEntry.init($0)
            } catch {
                print("Error parsing ShortableShareEntry: \(error)")
                return nil
            }
        })
        print("Shortable shares downloaded successfully")
        return ShortableShareFastDB(entries: rawData)
    }

    init(){
        do{
            self.database = try downloadShortableShares()
        } catch {
            print("WARNING: UNABLE TO DOWNLOAD SHORTABLE SHARES. THIS WILL BREAK SHORTABLE SHARES DATABASE")
            print("ERROR: \(error)")
        }
    }

    func conidToSymbol(_ conid: Int?) -> String? {
        guard let conid = conid else { return nil }
        guard let entry = database?.conidToEntry[conid] else { return nil }
        return database?.entries[entry].symbol
    }

    func symbolToConid(_ symbol: String?) -> Int? {
        guard let symbol = symbol else { return nil }
        guard let entry = database?.symbolToEntry[symbol] else { return nil }
        return database?.entries[entry].conid
    }


    /// Depreciated function only for API compatibility
    func translateSymbolToConid(_ symbol: String?) -> Int? {
        return symbolToConid(symbol)
    }
}
