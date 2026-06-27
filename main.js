#!/usr/bin/env node
'use strict';

const path = require('path');
const { RingMachine, carregarConfig } = require('./src/RingMachine');

function main() {
  const args = process.argv.slice(2).filter(a => a !== '--debug');
  const debugMode = process.argv.includes('--debug');

  if (args.length < 1) {
    console.error('Uso: node main.js <config.json> [apelido_override] [--debug]');
    console.error('Exemplo: node main.js config.example.json A --debug');
    process.exit(1);
  }

  const configPath = path.resolve(args[0]);
  const config = carregarConfig(configPath);

  if (args[1]) {
    config.apelido = args[1];
  }

  if (!config.apelido) {
    console.error('Config inválida: campo "apelido" é obrigatório.');
    process.exit(1);
  }

  config.debug = debugMode;

  const maquina = new RingMachine(config);
  maquina.start();

  process.on('SIGINT', () => {
    console.log('\nEncerrando...');
    process.exit(0);
  });
}

main();