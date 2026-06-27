'use strict';

const dgram = require('dgram');
const readline = require('readline');
const fs = require('fs');

const proto = require('./protocol');
const { getLocalIPv4 } = require('./netinfo');

const PORTA_PADRAO = 6000;

class RingMachine {
  constructor(config) {
    this.apelido = config.apelido;
    this.broadcastAddr = config.broadcastAddr || '255.255.255.255';
    this.porta = config.porta || PORTA_PADRAO;
    this.delayToken = config.delayToken ?? 1000;
    this.delayDados = config.delayDados ?? 500;
    this.probabilidadeErro = config.probabilidadeErro ?? 0;
    this.timeoutToken = config.timeoutToken ?? 8000;
    this.tempoMinimoToken = config.tempoMinimoToken ?? 500;

    this.ip = config.ip || getLocalIPv4();
    this.debug = config.debug || false;

    // hosts conhecidos: apelido -> { ip, ultimoHello: timestamp }
    this.hosts = new Map();
    this.hosts.set(this.apelido, { ip: this.ip, ultimoHello: Date.now() });

    this.sucessor = null; // apelido do sucessor atual
    this.souControladora = false;
    this.temToken = false;
    this.anelFormado = false;

    // fila FIFO de mensagens a enviar (máx 10)
    this.filaSaida = [];

    // número de sequência da próxima mensagem que esta máquina envia
    this.proximoSeqEnvio = 0;

    // próximo número de sequência esperado por origem (para detectar duplicatas)
    // origem -> próximo seq esperado
    this.seqEsperado = new Map();

    // timers de controle do token (só ativos se this.souControladora)
    this._timeoutTokenTimer = null;
    this._ultimaPassagemToken = 0;

    this._heartbeatTimer = null;
    this._checkHostsTimer = null;
    this._discoverRetryTimer = null;
    this._descobrindoDesde = null;

    this.socket = dgram.createSocket({ type: 'udp4', reuseAddr: true });
    this._setupSocket();
  }

  log(...args) {
    const ts = new Date().toISOString().split('T')[1].replace('Z', '');
    console.log(`[${ts}] [${this.apelido}]`, ...args);
  }

  dbg(...args) {
    if (!this.debug) return;
    const ts = new Date().toISOString().split('T')[1].replace('Z', '');
    console.log(`[${ts}] [${this.apelido}] [DEBUG]`, ...args);
  }

  wire(direction, addr, raw) {
    if (!this.debug) return;
    const ts = new Date().toISOString().split('T')[1].replace('Z', '');
    const tipo = raw.split(':')[0];
    const labels = { '10': 'DISCOVER', '20': 'HELLO', '1000': 'TOKEN', '2000': 'DADOS' };
    const label = labels[tipo] || '?';
    const arrow = direction === 'RX' ? '<--' : '-->';
    console.log(`[${ts}] [${this.apelido}] [${direction}] ${arrow} ${addr.padEnd(28)} ${label.padEnd(8)} ${raw}`);
  }

  // ---------------------------------------------------------------------
  // Inicialização de rede
  // ---------------------------------------------------------------------

  _setupSocket() {
    this.socket.on('error', (err) => {
      this.log('Erro de socket:', err.message);
    });

    this.socket.on('message', (msg, rinfo) => {
      this._onMessage(msg.toString('utf8'), rinfo);
    });

    this.socket.on('listening', () => {
      this.socket.setBroadcast(true);
      const addr = this.socket.address();
      this.log(`Escutando em ${addr.address}:${addr.port} (IP local detectado: ${this.ip})`);
    });
  }

  start() {
    this.socket.bind(this.porta, () => {
      this._iniciarDescoberta();
      this._iniciarHeartbeat();
      this._iniciarVerificacaoHosts();
      this._iniciarCLI();
    });
  }

  // ---------------------------------------------------------------------
  // Envio bruto
  // ---------------------------------------------------------------------

  _sendBroadcast(str) {
    const buf = Buffer.from(str, 'utf8');
    this.wire('TX', `${this.broadcastAddr}:${this.porta} (broadcast)`, str);
    this.socket.send(buf, 0, buf.length, this.porta, this.broadcastAddr, (err) => {
      if (err) this.log('Erro ao enviar broadcast:', err.message);
    });
  }

  _sendUnicast(str, ip) {
    const buf = Buffer.from(str, 'utf8');
    this.wire('TX', `${ip}:${this.porta} (unicast)`, str);
    this.socket.send(buf, 0, buf.length, this.porta, ip, (err) => {
      if (err) this.log('Erro ao enviar unicast para', ip, ':', err.message);
    });
  }

  // ---------------------------------------------------------------------
  // Descoberta do anel: DISCOVER / HELLO
  // ---------------------------------------------------------------------

  _iniciarDescoberta() {
    this._enviarDiscover();
  }

  _enviarDiscover() {
    this._descobrindoDesde = Date.now();
    this.log('Enviando DISCOVER em broadcast...');
    this._sendBroadcast(proto.buildDiscover(this.apelido, this.ip));

    // Aguarda 1s coletando HELLOs, depois forma o anel
    setTimeout(() => this._finalizarColetaHello(), 1000);

    // Se em 10s não recebeu nenhum HELLO de outra máquina, reenvia DISCOVER
    clearTimeout(this._discoverRetryTimer);
    this._discoverRetryTimer = setTimeout(() => {
      if (this.hosts.size <= 1) {
        this.log('Nenhum HELLO recebido em 10s. Reenviando DISCOVER...');
        this._enviarDiscover();
      }
    }, 10000);
  }

  _finalizarColetaHello() {
    const outros = this.hosts.size - 1;
    if (outros === 0) {
      this.log('Sozinha no anel. Aguardando outras máquinas (sem gerar token).');
      this.anelFormado = false;
      return;
    }
    this._reconstruirAnel();

    // Só a primeira vez (anel ainda não formado) decide quem gera o token inicial
    if (!this.anelFormado) {
      this.anelFormado = true;
      const apelidosOrdenados = this._apelidosOrdenados();
      if (apelidosOrdenados[0] === this.apelido) {
        this._tornarControladora();
        this.log('Sou a primeira em ordem alfabética. Gerando token inicial.');
        this.temToken = true;
        this._processarPosseToken();
      }
    }
  }

  _apelidosOrdenados() {
    return Array.from(this.hosts.keys()).sort();
  }

  /**
   * Recalcula o sucessor com base na lista ordenada de hosts conhecidos
   * e decide se esta máquina deve assumir como controladora do token.
   */
  _reconstruirAnel() {
    const ordenados = this._apelidosOrdenados();
    const idx = ordenados.indexOf(this.apelido);
    const sucessorAnterior = this.sucessor;
    this.sucessor = ordenados[(idx + 1) % ordenados.length];

    if (this.sucessor !== sucessorAnterior) {
      this.log(`Anel reconfigurado. Ordem: [${ordenados.join(', ')}]. Sucessor: ${this.sucessor}`);
    }

    const menorApelido = ordenados[0];
    if (menorApelido === this.apelido && !this.souControladora) {
      this._tornarControladora();
      this.log('Assumindo controle do token (menor apelido do anel). Gerando novo token.');
      this.temToken = true;
      setImmediate(() => this._processarPosseToken());
    } else if (menorApelido !== this.apelido && this.souControladora) {
      this.log('Cedendo controle do token (entrou máquina com apelido menor).');
      this._cessarControladora();
    }
  }

  // ---------------------------------------------------------------------
  // Heartbeat (HELLO periódico) e verificação de hosts inativos
  // ---------------------------------------------------------------------

  _iniciarHeartbeat() {
    this._heartbeatTimer = setInterval(() => {
      this._sendBroadcast(proto.buildHello(this.apelido, this.ip));
    }, 10000);
    // Envia o primeiro HELLO de si mesma imediatamente também,
    // útil para outras máquinas que já estejam ativas.
    this._sendBroadcast(proto.buildHello(this.apelido, this.ip));
  }

  _iniciarVerificacaoHosts() {
    this._checkHostsTimer = setInterval(() => {
      const agora = Date.now();
      let removeu = false;
      for (const [apelido, info] of this.hosts) {
        if (apelido === this.apelido) continue;
        if (agora - info.ultimoHello > 30000) {
          this.log(`Host ${apelido} inativo (sem HELLO há 30s). Removendo do anel.`);
          this.hosts.delete(apelido);
          this.seqEsperado.delete(apelido);
          removeu = true;
        }
      }
      if (removeu) {
        if (this.hosts.size <= 1) {
          this.sucessor = null;
          this.anelFormado = false;
          if (this.souControladora) this._cessarControladora();
        } else {
          this._reconstruirAnel();
        }
      }
    }, 5000);
  }

  // ---------------------------------------------------------------------
  // Recepção de mensagens
  // ---------------------------------------------------------------------

  _onMessage(raw, rinfo) {
    this.wire('RX', `${rinfo.address}:${rinfo.port}`, raw);
    const tipo = proto.peekTipo(raw);

    if (tipo === proto.TIPO.DISCOVER) {
      this._onDiscover(raw, rinfo);
    } else if (tipo === proto.TIPO.HELLO) {
      this._onHello(raw, rinfo);
    } else if (tipo === proto.TIPO.TOKEN) {
      this._onTokenRaw(raw);
    } else if (tipo === proto.TIPO.DADOS) {
      this._onDados(raw);
    } else {
      // Tipo desconhecido: ignorado silenciosamente.
    }
  }

  _onDiscover(raw, rinfo) {
    const d = proto.parseDiscover(raw);
    if (!d) return;
    if (d.apelido === this.apelido) return; // próprio DISCOVER, ignora

    const novo = !this.hosts.has(d.apelido);
    this.hosts.set(d.apelido, { ip: d.ip, ultimoHello: Date.now() });

    // Responde com HELLO em broadcast, como exigido pelo protocolo
    this._sendBroadcast(proto.buildHello(this.apelido, this.ip));

    if (novo) {
      this.log(`DISCOVER recebido de ${d.apelido} (${d.ip}). Respondendo HELLO e reconstruindo anel.`);
      this._reconstruirAnel();
    }
  }

  _onHello(raw, rinfo) {
    const h = proto.parseHello(raw);
    if (!h) return; // malformado estruturalmente
    if (!h.valido) return; // CRC inválido: descarte silencioso
    if (h.apelido === this.apelido) return; // próprio HELLO

    const novo = !this.hosts.has(h.apelido);
    this.hosts.set(h.apelido, { ip: h.ip, ultimoHello: Date.now() });

    if (novo) {
      this.log(`HELLO recebido de ${h.apelido} (${h.ip}). Novo host no anel.`);
      this._reconstruirAnel();
    }
  }

  // ---------------------------------------------------------------------
  // Token
  // ---------------------------------------------------------------------

  _tornarControladora() {
    this.souControladora = true;
    this._ultimaPassagemToken = Date.now();
    this._armarTimeoutToken();
  }

  _cessarControladora() {
    this.souControladora = false;
    clearTimeout(this._timeoutTokenTimer);
    this._timeoutTokenTimer = null;
  }

  _armarTimeoutToken() {
    clearTimeout(this._timeoutTokenTimer);
    if (!this.souControladora) return;
    this._timeoutTokenTimer = setTimeout(() => {
      this.log('TIMEOUT do token detectado. Gerando novo token.');
      this._ultimaPassagemToken = Date.now();
      this.temToken = true;
      this._processarPosseToken();
      this._armarTimeoutToken();
    }, this.timeoutToken);
  }

  /**
   * Trata o recebimento de um pacote bruto do tipo 1000.
   * Deve ser EXATAMENTE "1000" (sem campos extras) para ser
   * considerado token válido; caso contrário, é descartado
   * (proteção contra corrupção 2000 -> 1000).
   */
  _onTokenRaw(raw) {
    if (!proto.isTokenPuro(raw)) {
      this.log('Pacote tipo 1000 com campos extras recebido. Descartando (possível corrupção).');
      // O descarte no primeiro salto é o que dispara o timeout na controladora;
      // esta máquina não repassa o pacote corrompido.
      return;
    }
    this._onTokenRecebido();
  }

  _onTokenRecebido() {
    this.dbg('Token recebido.');
    if (this.souControladora) {
      const agora = Date.now();
      const intervalo = agora - this._ultimaPassagemToken;
      if (intervalo < this.tempoMinimoToken) {
        this.log(`Token duplicado detectado (intervalo ${intervalo}ms < ${this.tempoMinimoToken}ms). Descartando.`);
        return; // descarta o token extra, não repassa
      }
      this._ultimaPassagemToken = agora;
      this._armarTimeoutToken(); // rearma o timeout a cada passagem válida
    }

    this.temToken = true;
    this._processarPosseToken();
  }

  /**
   * Aplica o delay do token/dados configurado e decide a ação ao
   * receber a posse do token: enviar dado pendente ou passar o token.
   */
  _processarPosseToken() {
    if (!this.temToken) return;

    if (this.filaSaida.length === 0) {
      setTimeout(() => this._passarToken(), this.delayToken);
    } else {
      setTimeout(() => this._enviarProximaMensagem(), this.delayDados);
    }
  }

  _passarToken() {
    if (!this.temToken) return;
    if (!this.sucessor || this.sucessor === this.apelido) {
      // Anel com apenas esta máquina: não há para onde enviar.
      return;
    }
    const ipSucessor = this.hosts.get(this.sucessor)?.ip;
    if (!ipSucessor) {
      this.log(`Sucessor ${this.sucessor} sem IP conhecido. Reconstruindo anel.`);
      this._reconstruirAnel();
      this.temToken = false;
      return;
    }
    this.temToken = false;
    this.dbg(`Token repassado para ${this.sucessor} (${ipSucessor}).`);
    this._sendUnicast(proto.buildToken(), ipSucessor);
  }

  // ---------------------------------------------------------------------
  // Dados
  // ---------------------------------------------------------------------

  /**
   * Enfileira uma mensagem para envio (chamado a partir do stdin).
   * Fila limitada a 10 mensagens.
   */
  enfileirarMensagem(destino, mensagem) {
    if (this.filaSaida.length >= 10) {
      this.log('Fila cheia (máx 10 mensagens). Mensagem descartada.');
      return false;
    }
    if (mensagem.includes(':') || destino.includes(':')) {
      this.log('Mensagem ou apelido de destino não pode conter ":". Descartado.');
      return false;
    }
    this.filaSaida.push({
      destino,
      mensagem,
      seq: this.proximoSeqEnvio,
    });
    this.proximoSeqEnvio += 1;
    this.log(`Mensagem para ${destino} enfileirada (seq=${this.proximoSeqEnvio - 1}). Fila: ${this.filaSaida.length}/10.`);
    return true;
  }

  /**
   * Aplica a corrupção aleatória de mensagem conforme a probabilidade
   * configurada. Retorna a mensagem (possivelmente alterada).
   */
  _talvezCorromper(mensagem) {
    if (Math.random() < this.probabilidadeErro) {
      const corrompida = this._corromperString(mensagem);
      this.log(`[falha simulada] Mensagem corrompida antes do envio.`);
      return corrompida;
    }
    return mensagem;
  }

  _corromperString(str) {
    if (str.length === 0) return 'X';
    const pos = Math.floor(Math.random() * str.length);
    const chars = str.split('');
    // troca um caractere por outro, garantindo alteração e evitando ':'
    let novo;
    do {
      novo = String.fromCharCode(33 + Math.floor(Math.random() * 90));
    } while (novo === ':' || novo === chars[pos]);
    chars[pos] = novo;
    return chars.join('');
  }

  _enviarProximaMensagem() {
    if (!this.temToken) return;
    if (this.filaSaida.length === 0) {
      this._passarToken();
      return;
    }

    const item = this.filaSaida[0];
    const totalMaquinas = this.hosts.size;
    const ttl = totalMaquinas * 2;

    let mensagemFinal = this._talvezCorromper(item.mensagem);

    const pacote = proto.buildDados({
      origem: this.apelido,
      destino: item.destino,
      flag: 'maquinainexistente',
      seq: item.seq,
      ttl,
      mensagem: mensagemFinal,
    });

    const ipSucessor = this.hosts.get(this.sucessor)?.ip;
    if (!ipSucessor) {
      this.log(`Sucessor ${this.sucessor} desconhecido. Não é possível enviar dado agora.`);
      this.temToken = false;
      this._reconstruirAnel();
      return;
    }

    this.log(`Enviando DADOS seq=${item.seq} destino=${item.destino} via ${this.sucessor}.`);
    this.temToken = false; // só recupera o token quando o pacote retornar
    this._sendUnicast(pacote, ipSucessor);
  }

  _onDados(raw) {
    const d = proto.parseDados(raw);
    if (!d) {
      this.dbg('Pacote DADOS malformado (parse falhou). Descartando.');
      return;
    }

    const crcTag = d.valido ? 'CRC ok' : 'CRC INVALIDO';
    if (this.debug) {
      console.log(`[REDE] ${d.origem} -> ${d.destino} | flag=${d.flag} seq=${d.seq} ttl=${d.ttl} | ${crcTag} | "${d.mensagem}"`);
    }

    if (!d.valido) {
      // CRC inválido em qualquer salto: descartar imediatamente.
      // Isso dispara o timeout do token na controladora (o pacote nunca retorna).
      this.log(`Pacote de DADOS com CRC inválido recebido (origem=${d.origem}). Descartando.`);
      return;
    }

    if (d.ttl <= 0) {
      this.log(`Pacote de DADOS com TTL=0 (origem=${d.origem}). Descartando.`);
      return;
    }

    if (d.origem === this.apelido) {
      this._onDadosRetornouOrigem(d);
      return;
    }

    if (d.destino === this.apelido) {
      this._onDadosDestino(d);
      return;
    }

    // Apenas repassando: decrementa TTL e recomputa CRC.
    this._repassarDados(d, d.ttl - 1);
  }

  _repassarDados(d, novoTtl) {
    const ipSucessor = this.hosts.get(this.sucessor)?.ip;
    if (!ipSucessor) {
      this.log('Sucessor desconhecido ao repassar dados. Descartando pacote.');
      return;
    }
    const pacote = proto.buildDados({
      origem: d.origem,
      destino: d.destino,
      flag: d.flag,
      seq: d.seq,
      ttl: novoTtl,
      mensagem: d.mensagem,
    });
    this._sendUnicast(pacote, ipSucessor);
  }

  _onDadosDestino(d) {
    // d.valido já garantido pelo chamador
    let flagResposta;

    if (!this.seqEsperado.has(d.origem)) {
      this.seqEsperado.set(d.origem, 0);
    }
    const esperado = this.seqEsperado.get(d.origem);

    if (d.seq < esperado) {
      // duplicata: descarta conteúdo, responde ACK
      this.log(`DADOS duplicado de ${d.origem} (seq=${d.seq}, esperado=${esperado}). Respondendo ACK sem reprocessar.`);
      flagResposta = 'ACK';
    } else {
      console.log(`>> [${d.origem}]: ${d.mensagem}`);
      this.seqEsperado.set(d.origem, d.seq + 1);
      flagResposta = 'ACK';
    }

    const ttlResposta = this.hosts.size * 2;
    this._repassarDados({ ...d, flag: flagResposta }, ttlResposta);
  }

  _onDadosRetornouOrigem(d) {
    const item = this.filaSaida[0];

    if (!item || item.seq !== d.seq) {
      // Pacote referente a mensagem que já não é a cabeça da fila
      // (ex.: retorno tardio/duplicado). Reencaminha o token por segurança.
      this.log('Pacote de DADOS retornou mas não corresponde à mensagem atual da fila. Ignorando conteúdo.');
      this.temToken = true;
      this._processarPosseToken();
      return;
    }

    if (d.flag === 'ACK') {
      this.log(`ACK recebido para seq=${d.seq} (destino=${d.destino}). Mensagem entregue.`);
      this.filaSaida.shift();
    } else if (d.flag === 'maquinainexistente') {
      this.log(`Destino ${d.destino} inexistente/inativo para seq=${d.seq}. Descartando mensagem da fila.`);
      this.filaSaida.shift();
    } else if (d.flag === 'NAK') {
      this.log(`NAK recebido para seq=${d.seq} (destino=${d.destino}). Mensagem permanece na fila para retransmissão.`);
      // mantém item.seq na fila para reenviar na próxima posse do token
    } else {
      this.log(`Flag de retorno desconhecida (${d.flag}) para seq=${d.seq}. Tratando como NAK.`);
    }

    this.temToken = true;
    this._processarPosseToken();
  }

  // ---------------------------------------------------------------------
  // CLI (stdin) para enfileirar mensagens
  // ---------------------------------------------------------------------

  _iniciarCLI() {
    const rl = readline.createInterface({ input: process.stdin });
    this.log('Pronto. Comandos:');
    this.log('  enviar <destino> <mensagem>   - enfileira uma mensagem');
    this.log('  status                        - mostra estado do anel');
    rl.on('line', (linha) => {
      const trimmed = linha.trim();
      if (trimmed === 'status') {
        this._printStatus();
        return;
      }
      const m = trimmed.match(/^enviar\s+(\S+)\s+(.+)$/);
      if (m) {
        const [, destino, mensagem] = m;
        this.enfileirarMensagem(destino, mensagem);
      } else {
        this.log('Comando não reconhecido. Use: enviar <destino> <mensagem>  ou  status');
      }
    });
  }

  _printStatus() {
    console.log('--- STATUS ---');
    console.log('Apelido:', this.apelido, '| IP:', this.ip);
    console.log('Hosts conhecidos:', this._apelidosOrdenados().join(', '));
    console.log('Sucessor:', this.sucessor);
    console.log('Controladora do token:', this.souControladora);
    console.log('Possui o token agora:', this.temToken);
    console.log('Fila de saída:', this.filaSaida.map(m => `${m.destino}:${m.seq}`).join(', ') || '(vazia)');
    console.log('--------------');
  }
}

function carregarConfig(caminho) {
  const conteudo = fs.readFileSync(caminho, 'utf8');
  return JSON.parse(conteudo);
}

module.exports = { RingMachine, carregarConfig };