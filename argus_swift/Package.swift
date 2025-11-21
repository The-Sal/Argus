// swift-tools-version: 5.9
// The swift-tools-version declares the minimum version of Swift required to build this package.

import PackageDescription

let package = Package(
    name: "ArgusServer",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(
            name: "argus_server",
            targets: ["ArgusServer"]
        ),
    ],
    dependencies: [
        // No external dependencies - using native Swift URLSession WebSockets
    ],
    targets: [
        .executableTarget(
            name: "ArgusServer",
            dependencies: [],
            path: "Sources/ArgusServer"
        ),
    ]
)
