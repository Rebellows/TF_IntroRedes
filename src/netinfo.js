'use strict';

const os = require('os');

/**
 * Retorna o primeiro endereço IPv4 não-loopback encontrado nas
 * interfaces de rede da máquina. Usado para preencher o campo
 * <endereço IP da origem> nos pacotes DISCOVER/HELLO.
 */
function getLocalIPv4() {
  const interfaces = os.networkInterfaces();
  for (const nome of Object.keys(interfaces)) {
    for (const iface of interfaces[nome]) {
      if (iface.family === 'IPv4' && !iface.internal) {
        return iface.address;
      }
    }
  }
  return '127.0.0.1';
}

module.exports = { getLocalIPv4 };
