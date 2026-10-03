// Replay and freshness checks (proposed FR-06, 30 s window on GNSS time).
#include <unity.h>

#include <cstdio>

#include "replay_guard.h"

using namespace icvsp;

namespace {
const int64_t kNow = 1790000000000;
}

void setUp() {}
void tearDown() {}

void test_fresh_message_is_accepted_once() {
    ReplayGuard g;
    TEST_ASSERT_EQUAL(ReplayResult::Accept, g.check("dev-01", "dev-01-1", kNow - 500, kNow));
    TEST_ASSERT_EQUAL(ReplayResult::Duplicate, g.check("dev-01", "dev-01-1", kNow - 500, kNow + 1000));
    // replaying with the time field changed is also a duplicate (and would fail the signature anyway)
    TEST_ASSERT_EQUAL(ReplayResult::Duplicate, g.check("dev-01", "dev-01-1", kNow, kNow + 1000));
}

void test_same_id_from_another_sender_is_a_different_message() {
    ReplayGuard g;
    TEST_ASSERT_EQUAL(ReplayResult::Accept, g.check("dev-01", "7", kNow, kNow));
    TEST_ASSERT_EQUAL(ReplayResult::Accept, g.check("dev-02", "7", kNow, kNow));
}

void test_old_messages_are_stale() {
    ReplayGuard g;
    TEST_ASSERT_EQUAL(ReplayResult::Accept, g.check("dev-01", "a", kNow - 30000, kNow));
    TEST_ASSERT_EQUAL(ReplayResult::Stale, g.check("dev-01", "b", kNow - 30001, kNow));
}

void test_messages_from_the_future_are_refused() {
    ReplayGuard g;
    TEST_ASSERT_EQUAL(ReplayResult::Accept, g.check("dev-01", "a", kNow + 2000, kNow));  // clock skew
    TEST_ASSERT_EQUAL(ReplayResult::Future, g.check("dev-01", "b", kNow + 2001, kNow));
}

void test_window_is_configurable() {
    ReplayGuard g(5000);
    TEST_ASSERT_EQUAL(ReplayResult::Stale, g.check("dev-01", "a", kNow - 6000, kNow));
}

void test_old_ids_are_forgotten_after_the_window() {
    ReplayGuard g;
    g.check("dev-01", "a", kNow, kNow);
    TEST_ASSERT_EQUAL(1, g.size());
    // 31 s later the same message is refused as stale, and its slot is freed
    TEST_ASSERT_EQUAL(ReplayResult::Stale, g.check("dev-01", "a", kNow, kNow + 31000));
    TEST_ASSERT_EQUAL(ReplayResult::Accept, g.check("dev-01", "b", kNow + 31000, kNow + 31000));
    TEST_ASSERT_EQUAL(1, g.size());
}

void test_full_table_refuses_rather_than_forgets() {
    ReplayGuard g;
    char id[16];
    for (size_t i = 0; i < ReplayGuard::kCapacity; ++i) {
        std::snprintf(id, sizeof id, "m%zu", i);
        TEST_ASSERT_EQUAL(ReplayResult::Accept, g.check("dev-01", id, kNow, kNow));
    }
    TEST_ASSERT_EQUAL(ReplayResult::Busy, g.check("dev-01", "new", kNow, kNow));
    TEST_ASSERT_EQUAL(ReplayResult::Duplicate, g.check("dev-01", "m0", kNow, kNow));
    // once the window has passed there is room again
    TEST_ASSERT_EQUAL(ReplayResult::Accept, g.check("dev-01", "new", kNow + 31000, kNow + 31000));
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_fresh_message_is_accepted_once);
    RUN_TEST(test_same_id_from_another_sender_is_a_different_message);
    RUN_TEST(test_old_messages_are_stale);
    RUN_TEST(test_messages_from_the_future_are_refused);
    RUN_TEST(test_window_is_configurable);
    RUN_TEST(test_old_ids_are_forgotten_after_the_window);
    RUN_TEST(test_full_table_refuses_rather_than_forgets);
    return UNITY_END();
}
