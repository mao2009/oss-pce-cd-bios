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
    for (unsigned index = 0; index < Capacity; ++index)
        assert(ring.at(index) == index + 3);
}

int main() {
    check_capacity<1>();
    check_capacity<2>();
    check_capacity<127>();
    check_capacity<128>();
    check_capacity<256>();
}
