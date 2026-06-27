'use strict';

const { crc32 } = require('./crc32');

const TIPO = {
  DISCOVER: 10,
  HELLO: 20,
  TOKEN: 1000,
  DADOS: 2000,
};

/**
 * Monta string DISCOVER: 10:<apelido>:<ip>
 */
function buildDiscover(apelido, ip) {
  return `${TIPO.DISCOVER}:${apelido}:${ip}`;
}

/**
 * Monta string HELLO calculando o CRC32 sobre o campo vazio (terminado em ':').
 * 20:<apelido>:<ip>:<crc32>
 */
function buildHello(apelido, ip) {
  const semCrc = `${TIPO.HELLO}:${apelido}:${ip}:`;
  const crc = crc32(semCrc);
  return `${semCrc}${crc}`;
}

/**
 * Monta string do pacote de token: "1000" (sem campos adicionais).
 */
function buildToken() {
  return `${TIPO.TOKEN}`;
}

/**
 * Monta pacote de dados, calculando o CRC32 sobre todos os campos anteriores
 * com o campo de CRC vazio (terminado em ':').
 * 2000:<origem>:<destino>:<flag>:<seq>:<ttl>:<mensagem>:<crc32>
 */
function buildDados({ origem, destino, flag, seq, ttl, mensagem }) {
  const semCrc = `${TIPO.DADOS}:${origem}:${destino}:${flag}:${seq}:${ttl}:${mensagem}:`;
  const crc = crc32(semCrc);
  return `${semCrc}${crc}`;
}

/**
 * Identifica o tipo do pacote a partir da string bruta recebida,
 * sem validar conteúdo. Retorna null se não reconhecido.
 */
function peekTipo(raw) {
  const idx = raw.indexOf(':');
  const tipoStr = idx === -1 ? raw : raw.slice(0, idx);
  const tipo = Number(tipoStr);
  if (Number.isNaN(tipo)) return null;
  return tipo;
}

/**
 * Faz o parsing de um DISCOVER. Retorna {apelido, ip} ou null se malformado.
 */
function parseDiscover(raw) {
  const partes = raw.split(':');
  if (partes.length !== 3 || Number(partes[0]) !== TIPO.DISCOVER) return null;
  const [, apelido, ip] = partes;
  if (!apelido || !ip) return null;
  return { apelido, ip };
}

/**
 * Faz o parsing de um HELLO e valida o CRC32.
 * Retorna {apelido, ip, crc, valido} ou null se malformado estruturalmente.
 */
function parseHello(raw) {
  const partes = raw.split(':');
  if (partes.length !== 4 || Number(partes[0]) !== TIPO.HELLO) return null;
  const [tipo, apelido, ip, crcStr] = partes;
  if (!apelido || !ip || crcStr === undefined) return null;
  const crcRecebido = Number(crcStr);
  const semCrc = `${tipo}:${apelido}:${ip}:`;
  const crcCalculado = crc32(semCrc);
  return { apelido, ip, crc: crcRecebido, valido: crcRecebido === crcCalculado };
}

/**
 * Verifica se o pacote é um token "puro" (string exatamente "1000",
 * sem campos adicionais). Qualquer outra coisa começando com 1000:...
 * deve ser descartada (proteção contra corrupção 2000 -> 1000).
 */
function isTokenPuro(raw) {
  return raw === `${TIPO.TOKEN}`;
}

/**
 * Faz o parsing de um pacote de dados e valida o CRC32.
 * Retorna objeto com os campos e a flag `valido`, ou null se
 * estruturalmente malformado (número de campos incorreto).
 */
function parseDados(raw) {
  const partes = raw.split(':');
  if (partes.length !== 8 || Number(partes[0]) !== TIPO.DADOS) return null;
  const [tipo, origem, destino, flag, seqStr, ttlStr, mensagem, crcStr] = partes;
  if (!origem || !destino || !flag) return null;

  const seq = Number(seqStr);
  const ttl = Number(ttlStr);
  const crcRecebido = Number(crcStr);

  const semCrc = `${tipo}:${origem}:${destino}:${flag}:${seqStr}:${ttlStr}:${mensagem}:`;
  const crcCalculado = crc32(semCrc);

  return {
    origem,
    destino,
    flag,
    seq,
    ttl,
    mensagem,
    crc: crcRecebido,
    valido: crcRecebido === crcCalculado,
  };
}

module.exports = {
  TIPO,
  buildDiscover,
  buildHello,
  buildToken,
  buildDados,
  peekTipo,
  parseDiscover,
  parseHello,
  isTokenPuro,
  parseDados,
};
