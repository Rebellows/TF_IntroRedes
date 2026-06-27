'use strict';

/**
 * Implementação de CRC32/ISO-HDLC (CRC32b).
 * - Polinômio: 0xEDB88320 (forma refletida)
 * - Seed inicial: 0xFFFFFFFF
 * - XOR final: 0xFFFFFFFF
 * - Reflexão de entrada e saída
 *
 * Equivalente a binascii.crc32 (Python), java.util.zip.CRC32 (Java)
 * e crc32 do zlib (C). Não depende de bibliotecas externas.
 */

// Tabela pré-computada (256 entradas) para acelerar o cálculo byte a byte.
const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    }
    table[n] = c >>> 0;
  }
  return table;
})();

/**
 * Calcula o CRC32/ISO-HDLC de uma string (interpretada como UTF-8) ou Buffer.
 * @param {string|Buffer} data
 * @returns {number} inteiro decimal sem sinal de 32 bits
 */
function crc32(data) {
  const buf = Buffer.isBuffer(data) ? data : Buffer.from(String(data), 'utf8');
  let crc = 0xFFFFFFFF;
  for (let i = 0; i < buf.length; i++) {
    crc = CRC_TABLE[(crc ^ buf[i]) & 0xFF] ^ (crc >>> 8);
  }
  return (crc ^ 0xFFFFFFFF) >>> 0;
}

module.exports = { crc32 };
