#include "signer.h"

#include <cstring>

#include "monocypher-ed25519.h"

namespace icvsp {

namespace {

// Writes the bytes to be signed into buf; returns the length, or 0 if they do not fit.
size_t signed_bytes(const MessageHeader& h, const uint8_t* payload, size_t len, uint8_t* buf) {
    static const char kDomain[] = "ICVSP-v1";
    const size_t ls = std::strlen(h.sender), lm = std::strlen(h.msg_id);
    const size_t total = sizeof kDomain + ls + 1 + lm + 1 + 8 + len;  // sizeof includes the 0x00
    if (total > kMaxSignedBytes) return 0;
    uint8_t* p = buf;
    std::memcpy(p, kDomain, sizeof kDomain);
    p += sizeof kDomain;
    std::memcpy(p, h.sender, ls + 1);
    p += ls + 1;
    std::memcpy(p, h.msg_id, lm + 1);
    p += lm + 1;
    const uint64_t t = static_cast<uint64_t>(h.utc_ms);
    for (int i = 7; i >= 0; --i) *p++ = static_cast<uint8_t>(t >> (8 * i));
    if (len) std::memcpy(p, payload, len);
    return total;
}

}  // namespace

void keypair_from_seed(uint8_t seed[32], uint8_t secret_key[64], uint8_t public_key[32]) {
    crypto_ed25519_key_pair(secret_key, public_key, seed);
}

bool sign_message(const uint8_t secret_key[64], const MessageHeader& h, const uint8_t* payload, size_t len,
                  uint8_t signature[64]) {
    uint8_t buf[kMaxSignedBytes];
    const size_t n = signed_bytes(h, payload, len, buf);
    if (!n) return false;
    crypto_ed25519_sign(signature, secret_key, buf, n);
    return true;
}

bool verify_message(const uint8_t public_key[32], const MessageHeader& h, const uint8_t* payload, size_t len,
                    const uint8_t signature[64]) {
    uint8_t buf[kMaxSignedBytes];
    const size_t n = signed_bytes(h, payload, len, buf);
    if (!n) return false;
    return crypto_ed25519_check(signature, public_key, buf, n) == 0;
}

void to_hex(const uint8_t* data, size_t len, char* out) {
    static const char kDigits[] = "0123456789abcdef";
    for (size_t i = 0; i < len; ++i) {
        out[2 * i] = kDigits[data[i] >> 4];
        out[2 * i + 1] = kDigits[data[i] & 15];
    }
    out[2 * len] = '\0';
}

bool from_hex(const char* hex, uint8_t* out, size_t len) {
    if (std::strlen(hex) != 2 * len) return false;
    auto val = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    };
    for (size_t i = 0; i < len; ++i) {
        const int hi = val(hex[2 * i]), lo = val(hex[2 * i + 1]);
        if (hi < 0 || lo < 0) return false;
        out[i] = static_cast<uint8_t>(hi << 4 | lo);
    }
    return true;
}

}  // namespace icvsp
