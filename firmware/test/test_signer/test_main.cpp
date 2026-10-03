// Ed25519 signing (proposed NFR-10).
#include <unity.h>

#include <cstring>

#include "monocypher-ed25519.h"
#include "signer.h"

using namespace icvsp;

namespace {

const char* kSeedHex = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60";
const uint8_t kPayload[] = "{\"event_id\":\"dev-01-1\",\"type\":\"pothole\"}";
const MessageHeader kHeader{"dev-01", "dev-01-1", 1790000000123};

void keys(uint8_t sk[64], uint8_t pk[32]) {
    uint8_t seed[32];
    TEST_ASSERT_TRUE(from_hex(kSeedHex, seed, 32));
    keypair_from_seed(seed, sk, pk);
}

}  // namespace

void setUp() {}
void tearDown() {}

void test_rfc8032_vector_1() {
    // RFC 8032, section 7.1, TEST 1: proves this is standard Ed25519, verifiable by other libraries
    uint8_t sk[64], pk[32], sig[64];
    keys(sk, pk);
    char hex[129];
    to_hex(pk, 32, hex);
    TEST_ASSERT_EQUAL_STRING("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", hex);
    crypto_ed25519_sign(sig, sk, nullptr, 0);
    to_hex(sig, 64, hex);
    TEST_ASSERT_EQUAL_STRING(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
        hex);
}

void test_signed_message_verifies() {
    uint8_t sk[64], pk[32], sig[64];
    keys(sk, pk);
    TEST_ASSERT_TRUE(sign_message(sk, kHeader, kPayload, sizeof kPayload - 1, sig));
    TEST_ASSERT_TRUE(verify_message(pk, kHeader, kPayload, sizeof kPayload - 1, sig));
}

void test_any_change_breaks_the_signature() {
    uint8_t sk[64], pk[32], sig[64];
    keys(sk, pk);
    sign_message(sk, kHeader, kPayload, sizeof kPayload - 1, sig);

    uint8_t payload[sizeof kPayload];
    std::memcpy(payload, kPayload, sizeof kPayload);
    payload[30] ^= 1;  // one bit of the payload
    TEST_ASSERT_FALSE(verify_message(pk, kHeader, payload, sizeof payload - 1, sig));

    MessageHeader h = kHeader;
    h.msg_id = "dev-01-2";
    TEST_ASSERT_FALSE(verify_message(pk, h, kPayload, sizeof kPayload - 1, sig));
    h = kHeader;
    h.utc_ms += 1;  // replaying with a fresher time is detected
    TEST_ASSERT_FALSE(verify_message(pk, h, kPayload, sizeof kPayload - 1, sig));
    h = kHeader;
    h.sender = "dev-02";
    TEST_ASSERT_FALSE(verify_message(pk, h, kPayload, sizeof kPayload - 1, sig));

    uint8_t bad[64];
    std::memcpy(bad, sig, 64);
    bad[0] ^= 1;
    TEST_ASSERT_FALSE(verify_message(pk, kHeader, kPayload, sizeof kPayload - 1, bad));
}

void test_another_key_does_not_verify() {
    uint8_t sk[64], pk[32], sig[64], sk2[64], pk2[32], seed2[32];
    keys(sk, pk);
    std::memset(seed2, 7, 32);
    keypair_from_seed(seed2, sk2, pk2);
    sign_message(sk, kHeader, kPayload, sizeof kPayload - 1, sig);
    TEST_ASSERT_FALSE(verify_message(pk2, kHeader, kPayload, sizeof kPayload - 1, sig));
}

void test_seed_is_wiped() {
    uint8_t seed[32], sk[64], pk[32], zero[32] = {};
    std::memset(seed, 7, 32);
    keypair_from_seed(seed, sk, pk);
    TEST_ASSERT_EQUAL_MEMORY(zero, seed, 32);
}

void test_too_long_message_is_refused() {
    uint8_t sk[64], pk[32], sig[64];
    keys(sk, pk);
    static uint8_t big[kMaxSignedBytes];
    TEST_ASSERT_FALSE(sign_message(sk, kHeader, big, sizeof big, sig));
}

void test_hex_round_trip_and_bad_input() {
    const uint8_t data[4] = {0x00, 0x7f, 0xab, 0xff};
    char hex[9];
    to_hex(data, 4, hex);
    TEST_ASSERT_EQUAL_STRING("007fabff", hex);
    uint8_t back[4];
    TEST_ASSERT_TRUE(from_hex("007FABff", back, 4));
    TEST_ASSERT_EQUAL_MEMORY(data, back, 4);
    TEST_ASSERT_FALSE(from_hex("007fab", back, 4));
    TEST_ASSERT_FALSE(from_hex("007fabzz", back, 4));
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_rfc8032_vector_1);
    RUN_TEST(test_signed_message_verifies);
    RUN_TEST(test_any_change_breaks_the_signature);
    RUN_TEST(test_another_key_does_not_verify);
    RUN_TEST(test_seed_is_wiped);
    RUN_TEST(test_too_long_message_is_refused);
    RUN_TEST(test_hex_round_trip_and_bad_input);
    return UNITY_END();
}
