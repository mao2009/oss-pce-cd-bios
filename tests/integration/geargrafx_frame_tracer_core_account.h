#ifndef OSS_PCE_CD_BIOS_GEARGRAFX_FRAME_TRACER_CORE_ACCOUNT_H
#define OSS_PCE_CD_BIOS_GEARGRAFX_FRAME_TRACER_CORE_ACCOUNT_H

// Per-frame accounting of the pinned core's TraceLogger counters (Geargrafx b49ae82, read-only).
// Source facts this relies on (src/trace_logger.h, src/trace_logger.cpp at that revision):
//  - TraceLog() overwrites the oldest entry when full and increments both m_total_logged and
//    m_sequence once per entry.  m_count saturates at m_capacity.
//  - GetTotalLogged() is entries logged since the last Reset(); it is not capped by the capacity.
//  - Reset() zeroes position, count and total; it does NOT zero the sequence counter.
//  - SetCapacity(n) with n != capacity reallocates, discards the old log and calls Reset().
//  - The core never calls Reset() or SetCapacity() itself: only the tracer does, before frame 1.
// The sequence delta is therefore an independent cross-check: any Reset or capacity change between
// two samples (or a retained count that is not min(total, capacity)) makes that frame
// discontinuous, and its count is never guessed.

struct FrameTracerCoreSample {
    unsigned long long total;     // TraceLogger::GetTotalLogged()
    unsigned long long sequence;  // TraceLogger::GetSequence()
    unsigned long long capacity;  // TraceLogger::GetCapacity()
    unsigned long long count;     // TraceLogger::GetCount()
};

struct FrameTracerCoreFrame {
    bool continuous;            // counters advanced consistently since the previous sample
    unsigned long long logged;  // entries logged during this frame, before any capacity clamp (0 if !continuous)
    unsigned long long lost;    // of those, entries already overwritten at this sample (0 if !continuous)
};

class FrameTracerCoreAccount {
public:
    explicit FrameTracerCoreAccount(const FrameTracerCoreSample& start)
        : capacity_(start.capacity), previous_(start), frames_(0), accounted_(0), discontinuous_(0),
          gap_frames_(0), logged_(0), lost_(0), max_logged_(0) {}

    // A valid starting point is a just-reset logger with a non-zero capacity.
    static bool valid_start(const FrameTracerCoreSample& start) {
        return start.capacity != 0 && start.total == 0 && start.count == 0;
    }

    FrameTracerCoreFrame account(const FrameTracerCoreSample& now) {
        const unsigned long long retained = now.total < capacity_ ? now.total : capacity_;
        const bool continuous = now.capacity == capacity_ && now.total >= previous_.total &&
                                now.sequence >= previous_.sequence &&
                                now.sequence - previous_.sequence == now.total - previous_.total &&
                                now.count == retained;
        FrameTracerCoreFrame frame = {continuous, 0, 0};
        ++frames_;
        if (continuous) {
            frame.logged = now.total - previous_.total;
            frame.lost = frame.logged > capacity_ ? frame.logged - capacity_ : 0;
            ++accounted_;
            logged_ += frame.logged;
            lost_ += frame.lost;
            if (frame.lost) ++gap_frames_;
            if (frame.logged > max_logged_) max_logged_ = frame.logged;
        } else {
            ++discontinuous_;
        }
        previous_ = now;
        return frame;
    }

    unsigned long long capacity() const { return capacity_; }
    unsigned frames() const { return frames_; }
    unsigned accounted_frames() const { return accounted_; }
    unsigned discontinuous_frames() const { return discontinuous_; }
    unsigned gap_frames() const { return gap_frames_; }
    unsigned long long logged() const { return logged_; }
    unsigned long long lost() const { return lost_; }
    unsigned long long max_frame_logged() const { return max_logged_; }

private:
    unsigned long long capacity_;
    FrameTracerCoreSample previous_;
    unsigned frames_, accounted_, discontinuous_, gap_frames_;
    unsigned long long logged_, lost_, max_logged_;
};

#endif
