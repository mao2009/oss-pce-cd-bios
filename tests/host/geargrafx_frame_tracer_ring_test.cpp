#include "geargrafx_frame_tracer_ring.h"

#include <array>
#include <cassert>

template <unsigned Capacity>
void check_capacity() {
    FrameTracerRing<unsigned, 256> ring(Capacity);
    for (unsigned value = 0; value < Capacity + 3; ++value)
        ring.push(value);
    assert(ring.size() == Capacity);
    assert(ring.dropped() == 3);
    assert(ring.pushed() == Capacity + 3);
    assert(ring.pushed() == ring.size() + ring.dropped());
    for (unsigned index = 0; index < Capacity; ++index)
        assert(ring.at(index) == index + 3);
}

// Below capacity nothing is dropped and every push is retained.
void check_partial_fill() {
    FrameTracerRing<unsigned, 256> ring(8);
    assert(ring.pushed() == 0 && ring.size() == 0 && ring.dropped() == 0);
    for (unsigned value = 0; value < 5; ++value)
        ring.push(value);
    assert(ring.pushed() == 5 && ring.size() == 5 && ring.dropped() == 0);
}

int main() {
    check_partial_fill();
    check_capacity<1>();
    check_capacity<2>();
    check_capacity<127>();
    check_capacity<128>();
    check_capacity<256>();
}
