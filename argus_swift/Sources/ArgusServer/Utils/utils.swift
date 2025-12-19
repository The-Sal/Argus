import Foundation

func ignoreSIGPIPE() {
    var set = sigset_t()
    sigemptyset(&set)
    sigaddset(&set, SIGPIPE)
    pthread_sigmask(SIG_BLOCK, &set, nil)
}
