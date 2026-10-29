// Unit test for warm_control.h (standard C++ only; no Geargrafx). Build and run from the probe directory:
//   g++ -std=c++11 -Wall -Wextra -Wpedantic test_warm_control.cpp -o test_warm_control && ./test_warm_control
#include "warm_control.h"

#include <cstdio>
#include <string>
#include <vector>

namespace {

int failures = 0;
int checks = 0;

void expect(bool ok, const char* what)
{
    ++checks;
    if (!ok) {
        ++failures;
        std::printf("FAIL: %s\n", what);
    }
}

void expect_eq(const std::string& got, const std::string& want, const char* what)
{
    ++checks;
    if (got != want) {
        ++failures;
        std::printf("FAIL: %s\n  got:  [%s]\n  want: [%s]\n", what, got.c_str(), want.c_str());
    }
}

}  // namespace

int main()
{
    const std::string nonce = "abc12345_X-z";
    std::vector<std::string> sunk;
    warm_control::Writer w([&](const std::string& line) { sunk.push_back(line); }, nonce);

    // (a) normal path: seq on every line, 1 to 5 in order
    expect_eq(w.start(1), "@@WARM abc12345_X-z 1 START 1", "START 1 line");
    expect_eq(w.end(1, "COMPLETED"), "@@WARM abc12345_X-z 2 END 1 COMPLETED", "END 1 line");
    expect_eq(w.start(2), "@@WARM abc12345_X-z 3 START 2", "START 2 line");
    expect_eq(w.end(2, "COMPLETED"), "@@WARM abc12345_X-z 4 END 2 COMPLETED", "END 2 line");
    expect_eq(w.final("COMPLETED"), "@@WARM abc12345_X-z 5 FINAL COMPLETED", "FINAL line");
    expect(w.last_seq() == 5, "sequence advanced once per line");
    expect(sunk.size() == 5, "every written line was sunk once");

    // each line starts with the prefix and the nonce, and has no embedded newline
    bool prefix_ok = true;
    for (const std::string& line : sunk) {
        if (line.compare(0, 7 + nonce.size() + 1, "@@WARM " + nonce + " ") != 0)
            prefix_ok = false;
        if (line.find('\n') != std::string::npos)
            prefix_ok = false;
    }
    expect(prefix_ok, "prefix \"@@WARM <nonce> \" and no embedded newline");

    // the five allowed end states on END and FINAL; anything else is refused and writes nothing
    const char* states[] = {"COMPLETED", "BUDGET_EXCEEDED", "ABORTED", "BLOCKED"};
    for (const char* s : states) {
        expect(warm_control::valid_state(s), s);
    }
    expect(!warm_control::valid_state("TIMEOUT"), "the probe never writes TIMEOUT (runner decides it)");
    {
        // defence: END and FINAL refuse TIMEOUT and write nothing
        std::vector<std::string> sunk_t;
        warm_control::Writer wt([&](const std::string& line) { sunk_t.push_back(line); }, nonce);
        expect(wt.end(1, "TIMEOUT").empty(), "END 1 TIMEOUT refused");
        expect(wt.end(2, "TIMEOUT").empty(), "END 2 TIMEOUT refused");
        expect(wt.final("TIMEOUT").empty(), "FINAL TIMEOUT refused");
        expect(sunk_t.empty() && wt.last_seq() == 0, "refused TIMEOUT lines write nothing and do not advance seq");
    }
    expect(!warm_control::valid_state("NOT_STARTED"), "NOT_STARTED is not an output state");
    expect(!warm_control::valid_state("RUNNING"), "RUNNING is not an output state");
    expect(!warm_control::valid_state("WEIRD"), "unknown state refused");

    const std::size_t before = sunk.size();
    const unsigned seq_before = w.last_seq();
    expect(w.end(1, "NOT_STARTED").empty(), "END with NOT_STARTED refused");
    expect(w.final("WEIRD").empty(), "FINAL with an unknown state refused");
    expect(w.start(3).empty(), "START 3 refused");
    expect(w.end(0, "COMPLETED").empty(), "END 0 refused");
    expect(sunk.size() == before, "refused lines are not written");
    expect(w.last_seq() == seq_before, "refused lines do not advance the sequence");

    // (b) gate line: no seq, exact text, and it does not advance the sequence
    std::vector<std::string> sunk_b;
    warm_control::Writer wb([&](const std::string& line) { sunk_b.push_back(line); }, nonce);
    expect_eq(wb.gate_blocked("dynamic_mpr_detection_not_implemented"),
              "@@WARM abc12345_X-z FINAL BLOCKED dynamic_mpr_detection_not_implemented",
              "gate line exact text");
    expect(wb.last_seq() == 0, "gate line does not advance the sequence");
    expect(wb.gate_blocked("has space").empty(), "gate reason with a space refused");
    expect(wb.gate_blocked("").empty(), "empty gate reason refused");
    expect(sunk_b.size() == 1, "only the valid gate line was written");

    // nonce rules: 8 to 64 characters of [A-Za-z0-9_-]
    expect(warm_control::valid_nonce("abcdefgh"), "8 characters accepted");
    expect(warm_control::valid_nonce(std::string(64, 'a')), "64 characters accepted");
    expect(!warm_control::valid_nonce("abcdefg"), "7 characters refused");
    expect(!warm_control::valid_nonce(std::string(65, 'a')), "65 characters refused");
    expect(!warm_control::valid_nonce("abc defgh"), "space refused");
    expect(!warm_control::valid_nonce("abc@defgh"), "@ refused");
    expect(!warm_control::valid_nonce("abc\ndefgh"), "newline refused");
    expect(!warm_control::valid_nonce(""), "empty refused");

    std::printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
