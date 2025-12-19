import Foundation


// Doc: This doesnt work properly, it tries to work and we get a print out and then it just
// still kills the program
// func ignoreSIGPIPE() {
//     var set = sigset_t()
//     sigemptyset(&set)
//     sigaddset(&set, SIGPIPE)
//     pthread_sigmask(SIG_BLOCK, &set, nil)
// }


// Doc: This works on macOS perfectly but does not compile on Linux
// func ignoreSIGPIPE() {
//     var sa = sigaction()
//     sa.__sigaction_u = unsafeBitCast(SIG_IGN, to: __sigaction_u.self)
//     sa.sa_flags = 0
//     sigemptyset(&sa.sa_mask)
//     sigaction(SIGPIPE, &sa, nil)

//     print("[Signal] SIGPIPE ignored successfully")
// }

// Doc: This works on macOS and compiles on Linux. It has not been tested to stop exit on Linux (yet)
func ignoreSIGPIPE() {
    signal(SIGPIPE, SIG_IGN)
    print("[Signal] SIGPIPE ignored successfully")
}
