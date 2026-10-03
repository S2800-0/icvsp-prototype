#pragma once
#include <cstddef>
#include <cstdint>

namespace icvsp {

// Ed25519 signatures on reports and beacons (proposed NFR-10), using Monocypher's standard Ed25519
// (RFC 8032, SHA-512), so the backend can verify with any common library (Python `cryptography`, libsodium).
//
// The signed bytes are
//     "ICVSP-v1" 0x00 sender 0x00 msg_id 0x00 utc_ms(8 bytes, big-endian) payload
// so the sender, message ID and time used by the replay check are covered by the signature and can be
// checked without parsing the payload. The fixed prefix keeps these signatures from being valid for
// anything else signed with the same key.
//
// Key storage and registration are out of scope here (cybersecurity team): on the ESP32-S3 the private
// key should live in encrypted flash with secure boot enabled.

struct MessageHeader {
    const char* sender;
    const char* msg_id;
    int64_t utc_ms;
};

constexpr size_t kMaxSignedBytes = 1024;

// Derives the key pair from a 32-byte random seed. The seed is wiped.
void keypair_from_seed(uint8_t seed[32], uint8_t secret_key[64], uint8_t public_key[32]);

// Returns false if the message is too long (more than kMaxSignedBytes in total).
bool sign_message(const uint8_t secret_key[64], const MessageHeader& h, const uint8_t* payload, size_t len,
                  uint8_t signature[64]);

bool verify_message(const uint8_t public_key[32], const MessageHeader& h, const uint8_t* payload, size_t len,
                    const uint8_t signature[64]);

// Lower-case hex. out must hold 2*len+1 characters.
void to_hex(const uint8_t* data, size_t len, char* out);
// Returns false on bad length or characters.
bool from_hex(const char* hex, uint8_t* out, size_t len);

}  // namespace icvsp
