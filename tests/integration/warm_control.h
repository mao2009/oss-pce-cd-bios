// Control lines for the warm probe (Phase 2, design section 6). Standard C++ only: no Geargrafx dependency, so the
// line format and the sequence numbers are unit-tested in isolation (test_warm_control.cpp).
//
// Lines are written to stdout as "@@WARM <nonce> <seq> START|END|FINAL ..." and each one is flushed by the sink.
// The gate line for contract 14.13 option (b) has no seq: "@@WARM <nonce> FINAL BLOCKED <reason>".
#ifndef WARM_CONTROL_H
#define WARM_CONTROL_H

#include <functional>
#include <string>
#include <utility>

namespace warm_control {

// nonce: 8 to 64 characters of [A-Za-z0-9_-]. The probe exits 2 on any other value.
inline bool valid_nonce(const std::string& nonce)
{
    if (nonce.size() < 8 || nonce.size() > 64)
        return false;
    for (char c : nonce) {
        const bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' ||
                        c == '-';
        if (!ok)
            return false;
    }
    return true;
}

// The states the probe may write on END and FINAL lines. TIMEOUT is never written by the probe: the runner decides
// it from the windows (contract 6.1, phase 2 stage 3).
inline bool valid_state(const std::string& s)
{
    return s == "COMPLETED" || s == "BUDGET_EXCEEDED" || s == "ABORTED" || s == "BLOCKED";
}

class Writer {
public:
    // sink receives one complete line without a newline; the probe's sink prints it and flushes stdout.
    Writer(std::function<void(const std::string&)> sink, std::string nonce)
        : sink_(std::move(sink)), nonce_(std::move(nonce))
    {
    }

    // Each function returns the line it wrote, or "" when the arguments are invalid (nothing is written and the
    // sequence number is not advanced).
    std::string start(int trial)
    {
        if (trial != 1 && trial != 2)
            return std::string();
        return emit("START " + std::to_string(trial));
    }

    std::string end(int trial, const std::string& state)
    {
        if ((trial != 1 && trial != 2) || !valid_state(state))
            return std::string();
        return emit("END " + std::to_string(trial) + " " + state);
    }

    std::string final(const std::string& state)
    {
        if (!valid_state(state))
            return std::string();
        return emit("FINAL " + state);
    }

    // contract 14.13 option (b): no seq, and it does not advance the sequence
    std::string gate_blocked(const std::string& reason)
    {
        if (reason.empty() || reason.find(' ') != std::string::npos)
            return std::string();
        const std::string line = "@@WARM " + nonce_ + " FINAL BLOCKED " + reason;
        sink_(line);
        return line;
    }

    unsigned last_seq() const { return seq_; }

private:
    std::string emit(const std::string& body)
    {
        const unsigned seq = seq_ + 1;
        const std::string line = "@@WARM " + nonce_ + " " + std::to_string(seq) + " " + body;
        seq_ = seq;
        sink_(line);
        return line;
    }

    std::function<void(const std::string&)> sink_;
    std::string nonce_;
    unsigned seq_ = 0;
};

}  // namespace warm_control

#endif  // WARM_CONTROL_H
