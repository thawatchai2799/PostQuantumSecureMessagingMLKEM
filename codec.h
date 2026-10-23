/* codec.h — S1/S2 codec public interface (no I/O, no big buffers). */
#ifndef CODEC_H
#define CODEC_H
#include <stddef.h>
#include <stdint.h>

void codec_init(void);   /* build all tables; call once */

long s1_encode(const uint8_t *in, size_t n, uint8_t *out);
long s1_decode(const uint8_t *in, size_t n, uint8_t *out);

typedef enum { S2_TABLE_EN, S2_TABLE_C2, S2_TABLE_C3 } s2_table_id;
long s2_encode_id(s2_table_id id, const uint8_t *in, size_t n, uint8_t *out);
long s2_decode_id(s2_table_id id, const uint8_t *in, size_t n, uint8_t *out);
/* bit-serial reference decoder above; table-driven (9-bit lookup) below.
 * Both return identical output; they differ only in speed and in the
 * 1 KiB lookup table per code table. */
long s2_decode_lut_id(s2_table_id id, const uint8_t *in, size_t n, uint8_t *out);
#endif
