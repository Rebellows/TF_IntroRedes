'use strict';

const os = require('os');

/**
  * Retorna o endereço IPv4 local da máquina, ou 
  * `127.0.0.1` se nenhuma interface for encontrada.
  * Loopback e endereços internos são ignorados.
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
