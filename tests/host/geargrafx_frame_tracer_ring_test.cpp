#include "geargrafx_frame_tracer_ring.h"

#include <array>
#include <cassert>
#include <type_traits>
#include <utility>

// dropped() and pushed() share one 64-bit width, so pushed == size + dropped cannot wrap apart.
static_assert(std::is_same<decltype(std::declval<FrameTracerRing<unsigned, 4>>().dropped()),
                           unsigned long long>::value, "dropped() must be 64-bit");
static_assert(std::is_same<decltype(std::declval<FrameTracerRing<unsigned, 4>>().pushed()),
                           unsigned long long>::value, "pushed() must be 64-bit");

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
