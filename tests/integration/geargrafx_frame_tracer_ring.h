#ifndef OSS_PCE_CD_BIOS_GEARGRAFX_FRAME_TRACER_RING_H
#define OSS_PCE_CD_BIOS_GEARGRAFX_FRAME_TRACER_RING_H

#include <array>

// Fixed-storage ring used by the local frame tracer.  The runtime capacity may
// be smaller than MaxCapacity, so every index must use capacity_.
template <typename T, unsigned MaxCapacity>
class FrameTracerRing {
public:
    explicit FrameTracerRing(unsigned capacity) : capacity_(capacity), begin_(0), size_(0), dropped_(0) {}

    void push(const T& value) {
        if (size_ < capacity_) {
            entries_[(begin_ + size_) % capacity_] = value;
            ++size_;
            return;
        }
        entries_[begin_] = value;
        begin_ = (begin_ + 1) % capacity_;
        ++dropped_;
    }

    unsigned size() const { return size_; }
    unsigned dropped() const { return dropped_; }
    const T& at(unsigned index) const { return entries_[(begin_ + index) % capacity_]; }

private:
    std::array<T, MaxCapacity> entries_{};
    unsigned capacity_, begin_, size_, dropped_;
};

#endif
