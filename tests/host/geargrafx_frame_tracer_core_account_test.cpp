#include "geargrafx_frame_tracer_core_account.h"

#include <cassert>

// Counter model of the pinned TraceLogger (Geargrafx b49ae82 src/trace_logger.{h,cpp}):
// TraceLog increments total and sequence and saturates count; Reset zeroes total and count only;
// SetCapacity to a new size reallocates and calls Reset.  No core code is copied or linked.
struct CounterModel {
    unsigned long long capacity, total, sequence, count;
    void log(unsigned long long entries) {
        total += entries;
        sequence += entries;
        count = count + entries < capacity ? count + entries : capacity;
    }
    void reset() { total = count = 0; }
    void set_capacity(unsigned long long value) {
        if (value != capacity) { capacity = value; reset(); }
    }
    FrameTracerCoreSample sample() const { return {total, sequence, capacity, count}; }
};

CounterModel fresh(unsigned long long capacity) {
    CounterModel model = {capacity, 0, 7, 0};  // the sequence survives earlier Resets: start non-zero
    return model;
}

void check_normal_and_capacity_boundary() {
    CounterModel core = fresh(10);
    assert(FrameTracerCoreAccount::valid_start(core.sample()));
    FrameTracerCoreAccount account(core.sample());
    core.log(0);  // empty frame
    FrameTracerCoreFrame frame = account.account(core.sample());
    assert(frame.continuous && frame.logged == 0 && frame.lost == 0);
    core.log(10);  // exactly the capacity: nothing is lost
    frame = account.account(core.sample());
    assert(frame.continuous && frame.logged == 10 && frame.lost == 0);
    core.log(11);  // one past the capacity: exactly one entry is lost
    frame = account.account(core.sample());
    assert(frame.continuous && frame.logged == 11 && frame.lost == 1);
    core.log(3);
    frame = account.account(core.sample());
    assert(frame.continuous && frame.logged == 3 && frame.lost == 0);
    assert(account.frames() == 4 && account.accounted_frames() == 4 && account.discontinuous_frames() == 0);
    assert(account.gap_frames() == 1 && account.lost() == 1 && account.logged() == 24);
    assert(account.max_frame_logged() == 11);  // pre-clamp: larger than the capacity
    assert(account.logged() == core.total);
}

void check_forced_overflow_every_frame() {
    CounterModel core = fresh(1);
    FrameTracerCoreAccount account(core.sample());
    for (unsigned frame = 0; frame != 5; ++frame) {
        core.log(1000 + frame);
        const FrameTracerCoreFrame result = account.account(core.sample());
        assert(result.continuous && result.logged == 1000 + frame && result.lost == 999 + frame);
    }
    assert(account.gap_frames() == 5 && account.max_frame_logged() == 1004);
    assert(account.lost() + 5 == account.logged());  // one retained (and consumed) entry per frame
}

void check_unexpected_reset_is_discontinuous_then_resumes() {
    CounterModel core = fresh(100);
    FrameTracerCoreAccount account(core.sample());
    core.log(50);
    assert(account.account(core.sample()).continuous);
    core.log(20);
    core.reset();  // total drops to 0, sequence keeps counting
    core.log(5);
    FrameTracerCoreFrame frame = account.account(core.sample());
    assert(!frame.continuous && frame.logged == 0 && frame.lost == 0);
    core.log(8);
    frame = account.account(core.sample());
    assert(frame.continuous && frame.logged == 8);
    assert(account.discontinuous_frames() == 1 && account.accounted_frames() == 2 && account.logged() == 58);
}

void check_reset_that_does_not_lower_total_is_still_detected() {
    // Reset followed by more logging than before: total does not decrease, but the sequence
    // delta exceeds the total delta, so the frame is still discontinuous.
    CounterModel core = fresh(1000);
    FrameTracerCoreAccount account(core.sample());
    core.log(5);
    assert(account.account(core.sample()).continuous);
    core.reset();
    core.log(9);
    assert(!account.account(core.sample()).continuous);
}

void check_capacity_change_is_discontinuous_for_the_rest_of_the_run() {
    CounterModel core = fresh(100);
    FrameTracerCoreAccount account(core.sample());
    core.log(10);
    assert(account.account(core.sample()).continuous);
    core.set_capacity(50);
    core.log(10);
    assert(!account.account(core.sample()).continuous);
    core.log(10);
    assert(!account.account(core.sample()).continuous);  // capacity no longer matches the start
    assert(account.discontinuous_frames() == 2 && account.accounted_frames() == 1);
}

void check_inconsistent_counters_are_discontinuous() {
    CounterModel core = fresh(100);
    FrameTracerCoreAccount account(core.sample());
    core.log(10);
    FrameTracerCoreSample bad = core.sample();
    bad.count = 9;  // retained count is not min(total, capacity)
    assert(!account.account(bad).continuous);
    core.log(10);
    bad = core.sample();
    bad.sequence -= 1;  // sequence delta != total delta
    assert(!account.account(bad).continuous);
    core.log(1);
    assert(!account.account(core.sample()).continuous);  // still measured against the bad sample
    core.log(1);
    assert(account.account(core.sample()).continuous);  // resynchronized
    assert(account.discontinuous_frames() == 3 && account.accounted_frames() == 1 && account.logged() == 1);
}

void check_counters_wound_back_together_are_discontinuous() {
    // Both counters moving backwards by the same amount gives equal (wrapped) unsigned deltas;
    // the monotonicity guards must still reject it rather than report ~2^64 logged entries.
    CounterModel core = fresh(100);
    FrameTracerCoreAccount account(core.sample());
    core.log(70);
    assert(account.account(core.sample()).continuous);
    FrameTracerCoreSample back = core.sample();
    back.total -= 10;
    back.sequence -= 10;
    back.count = back.total < back.capacity ? back.total : back.capacity;
    const FrameTracerCoreFrame frame = account.account(back);
    assert(!frame.continuous && frame.logged == 0);
    assert(account.max_frame_logged() == 70 && account.logged() == 70);
}

void check_invalid_starts() {
    assert(!FrameTracerCoreAccount::valid_start({0, 0, 0, 0}));  // zero capacity
    assert(!FrameTracerCoreAccount::valid_start({1, 1, 10, 1}));  // not reset
    assert(!FrameTracerCoreAccount::valid_start({0, 3, 10, 1}));  // retained entries after reset
    assert(FrameTracerCoreAccount::valid_start({0, 99, 10, 0}));  // sequence is never reset
}

int main() {
    check_normal_and_capacity_boundary();
    check_forced_overflow_every_frame();
    check_unexpected_reset_is_discontinuous_then_resumes();
    check_reset_that_does_not_lower_total_is_still_detected();
    check_capacity_change_is_discontinuous_for_the_rest_of_the_run();
    check_inconsistent_counters_are_discontinuous();
    check_counters_wound_back_together_are_discontinuous();
    check_invalid_starts();
}
