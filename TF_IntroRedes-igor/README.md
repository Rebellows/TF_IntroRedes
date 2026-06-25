# Token Ring sobre UDP — Trabalho Final de Fundamentos de Redes

Simulação de uma rede local em **anel** com passagem de **token** sobre **UDP**.
Cada máquina é um nó do anel; o token circula controlando quem pode transmitir.
A topologia é montada dinamicamente por broadcast (DISCOVER/HELLO) e reordenada
em ordem alfabética de apelido.

- Transporte: **UDP, porta 6000**
- Broadcast para `DISCOVER` e `HELLO`; unicast para `token` e `dados`
- CRC32/ISO-HDLC em HELLOs e pacotes de dados
- Detecção de token perdido/duplicado, injeção de falhas, retransmissão por NAK
- Entrada/saída de máquinas em tempo real, sem reiniciar

---

## 1. Requisitos

- **Python 3.10+** (usa `tipo | None`); sem bibliotecas externas — só a stdlib.
- As máquinas precisam estar na **mesma sub-rede** e o **broadcast** precisa
  funcionar entre elas (rede local cabeada/Ethernet ou Wi-Fi no mesmo switch/AP).
- A **porta UDP 6000** precisa estar liberada no firewall de cada máquina.

---

## 2. Configuração (`config.ini`)

Cada máquina tem o **seu próprio** `config.ini`. Só o `nickname` muda entre elas;
os tempos devem ser **iguais em todas**.

```ini
[machine]
nickname = A
# ip = 192.168.1.10   # opcional: force o IP se a detecção automática errar

[timing]
token_delay = 0.5         # atraso antes de passar o token / enviar dados
data_delay = 0.1          # atraso antes de encaminhar dados
token_timeout = 5.0       # sem token na controladora por mais que isto → gera novo
min_token_interval = 0.5  # token mais rápido que isto na controladora → duplicata

[faults]
error_probability = 0.1   # probabilidade de corromper a mensagem antes de enviar
```

### ⚠️ Regra de ouro dos tempos (não ignore)

O token dá uma **volta completa** no anel a cada `nº de máquinas × token_delay`.
Para o anel não travar, vale:

```
min_token_interval  <  (nº de máquinas × token_delay)  <  token_timeout
```

- Se `min_token_interval ≥ volta`, **toda volta legítima do token vira "duplicata"
  e é descartada** → o anel entra em loop de `Token lost / Duplicate token`.
- Se `token_timeout ≤ volta`, a controladora gera um token novo antes do antigo
  voltar → tokens duplicados.

Com **2 máquinas** e `token_delay = 0.5` → volta ≈ 1,0 s, confortavelmente entre
`min_token_interval = 0.5` e `token_timeout = 5.0`. ✅
O programa **avisa no log** (ERROR) se essa regra for violada ao virar controladora.

---

## 3. Como executar em máquinas reais (cenário da apresentação)

Em **cada** máquina, dentro da pasta do projeto:

1. Ajuste o `nickname` no `config.ini` (`A`, `B`, `C`, ...).
2. Garanta que os tempos são iguais aos das outras (veja a regra de ouro).
3. Rode:

```bash
python3 main.py
```

A primeira linha confirma **qual arquivo** foi lido e os valores carregados:

```
>>> Config lido de: /caminho/real/config.ini
Loaded config: Config(nickname='A', ip='', token_delay=0.5, ...)
```

> Sempre confira essa linha. Se o `token_delay` não for o que você editou, você
> está rodando de **outra pasta** (veja Troubleshooting).

A primeira máquina em ordem alfabética vira a **controladora** e gera o token inicial.

---

## 4. Enviar mensagens

Com o nó rodando, digite no terminal:

```
> B Olá, mundo
```

Formato: `<apelido_do_destino> <mensagem>`

- O destino é **normalizado para maiúsculo** (`b` = `B`).
- A mensagem e o apelido **não podem conter `:`** (separador dos pacotes).
- A fila guarda até **10 mensagens** (FIFO). A mensagem só sai da fila após
  `ACK` ou `maquinainexistente`; em `NAK` ela é **retransmitida** na próxima
  passagem do token (mesmo número de sequência).
- Digite `quit` para encerrar.

---

## 5. Dica de observabilidade (recomendado na demo — Critério 7)

Os logs vão para `stderr` e podem atrapalhar a digitação. Separe-os em um arquivo
e acompanhe em outro terminal:

```bash
# Terminal 1 — você digita as mensagens (tela limpa)
python3 main.py 2> no.log

# Terminal 2 — acompanha token, ACK/NAK, falhas em tempo real
tail -f no.log
```

Nível de log detalhado para depuração:

```bash
python3 main.py --log DEBUG
```

---

## 6. Teste local (uma só máquina, com Docker)

Para testar a lógica sem 3 computadores, use os containers já configurados
(sub-rede `172.20.0.0/24`, broadcast interno):

```bash
docker compose up --build          # sobe ring-A, ring-B, ring-C
```

Em três terminais separados:

```bash
docker attach ring-A
docker attach ring-B
docker attach ring-C
```

Para desanexar sem parar o container: **Ctrl-P, Ctrl-Q**.
Parar um nó (testa token perdido / saída de máquina): `docker stop ring-B`.
Derrubar tudo: `docker compose down`.

---

## 7. Formato dos pacotes (compatível com a especificação)

| Pacote   | Formato                                                        | Exemplo |
|----------|----------------------------------------------------------------|---------|
| DISCOVER | `10:<apelido>:<ip>`                                            | `10:A:10.32.143.20` |
| HELLO    | `20:<apelido>:<ip>:<CRC32>`                                    | `20:B:10.32.143.21:48291734` |
| Token    | `1000`                                                         | `1000` |
| Dados    | `2000:<orig>:<dest>:<flag>:<seq>:<ttl>:<msg>:<CRC32>`          | `2000:B:A:ACK:42:8:Oi:19385749` |

- **Flags:** `maquinainexistente` (em trânsito), `ACK`, `NAK`.
- **Token com campos extras** (`1000:...`) é descartado imediatamente (protege
  contra corrupção de `2000` → `1000`).
- **CRC32/ISO-HDLC** (`binascii.crc32(...) & 0xFFFFFFFF`), calculado com o campo
  CRC vazio (string terminada em `:`). Verificado **em todo salto**.
- **TTL:** origem define `2 × nº de máquinas`; cada salto intermediário decrementa;
  destino reseta para `2 × máquinas conhecidas`. TTL = 0 → descarta (dispara timeout).

---

## 8. Como o anel funciona

**Inicialização.** Ao iniciar, o nó envia `DISCOVER` + `HELLO` em broadcast e
aguarda ~1 s pelos HELLOs. Monta a lista em ordem alfabética e define o sucessor
(circular). O menor apelido vira controladora e gera o token. Sozinho, espera sem
gerar token. Sem nenhum HELLO em 10 s, reenvia o DISCOVER.

**Circulação do token.** Quem recebe o token: se a fila está vazia, repassa ao
sucessor; se tem mensagem, envia o pacote de dados e **aguarda o retorno** antes
de repassar o token.

**No destino.** Verifica o CRC:
- CRC inválido → responde `NAK` (reset TTL, recomputa CRC, reenvia).
- CRC válido → confere o número de sequência; se novo, **imprime** e marca `ACK`;
  se duplicado, responde `ACK` sem imprimir. Reseta o TTL e encaminha.

**Na origem (pacote voltou).** Revalida o CRC (inválido = trata como `NAK`):
- `ACK` → exibe, remove da fila, passa o token.
- `maquinainexistente` → destino não existe; exibe, remove da fila, passa o token.
- `NAK` → exibe, **mantém na fila** e retransmite na próxima passagem do token.

**Perda de pacote.** Se o pacote enviado nunca volta (descartado por CRC/TTL em
algum salto), o token reaparece na origem ainda "aguardando retorno": isso é
detectado como perda e a mensagem é **retransmitida** com o mesmo número de
sequência.

**Controle do token (controladora).**
- *Token perdido:* sem token por mais que `token_timeout` → gera um novo.
- *Token duplicado:* token mais rápido que `min_token_interval` → descarta o extra.
- Se entra uma máquina com apelido **menor**, ela assume o controle; a anterior
  para de monitorar.

**Falhas.** Antes de enviar dados, com `error_probability` a mensagem é corrompida
(o CRC antigo é mantido), ficando inválida no receptor — exercita o caminho de NAK.

**Mudança de topologia.** Entrada: novo `DISCOVER`/`HELLO` reconstrói o anel.
Saída: detectada por **heartbeat** — HELLO a cada 10 s; sem HELLO válido por 30 s,
o host é removido e o anel é reconstruído. Nenhum protocolo de saída explícito.

---

## 9. Arquitetura do código

| Arquivo                | Responsabilidade |
|------------------------|------------------|
| `main.py`              | Entrada; lê config, configura log, inicia o nó |
| `config.py`            | `Config` — lê/valida `config.ini`; expõe `path` do arquivo lido |
| `constants.py`         | Porta, endereço de broadcast, tipos de pacote, flags, tempos |
| `network.py`           | `UDPSocket` — envio (broadcast/unicast) e thread de recepção |
| `ring.py`              | `RingTopology`/`Peer` — peers, ordem alfabética, sucessor, prune |
| `queue_manager.py`     | `MessageQueue` (FIFO, máx. 10) e `SequenceTracker` (duplicatas) |
| `crc.py`               | CRC32/ISO-HDLC; montar/verificar HELLO e pacotes de dados |
| `faults.py`            | `maybe_corrupt` — injeção de falhas |
| `token_controller.py`  | `TokenController` — timeout e duplicata do token |
| `node.py`              | `Node` — orquestra descoberta, token, dados, heartbeat |

**Threads e sincronização**

- *Main:* roda `start()` e mantém o processo vivo.
- *Receptor UDP* (`network.py`): lê datagramas e despacha para os handlers.
- *Input* (`node.py`): lê o `stdin` e enfileira mensagens.
- *Heartbeat* e *prune* (`threading.Timer`): reenviam HELLO e removem mortos.
- *Monitor do token* (`token_controller.py`): só na controladora.
- Estado compartilhado protegido por `threading.Lock` (tabela de peers, fila,
  ciclo de vida da controladora).

---

## 10. Troubleshooting (problemas que já enfrentamos)

| Sintoma | Causa | Solução |
|---|---|---|
| Loop infinito `Token lost! / Duplicate token detected` | `min_token_interval ≥ volta do token` (ex.: `token_delay=0.1` com 2–3 nós) | Aumente `token_delay` (use `0.5`); respeite a **regra de ouro** (§2). O log mostra um ERROR explicando. |
| Editei o `config.ini` mas o `Loaded config` mostra outro valor | Você editou **uma cópia** e executa de **outra pasta** | Veja `>>> Config lido de:` na 1ª linha. Edite o arquivo daquela pasta: `sed -i 's/^token_delay.*/token_delay = 0.5/' config.ini` |
| Um nó acha o outro, mas não o contrário | Detecção de IP falhava sem rota de internet (anunciava `127.0.0.1`) | Já corrigido: o HELLO usa o IP real do datagrama; `get_own_ip()` testa vários gateways. Se ainda errar, fixe `ip = ...` no `config.ini`. |
| `[NOROUTE] x not found` | Destino digitado em minúsculo ou máquina realmente ausente | Apelidos são normalizados para maiúsculo; confirme que o destino está no anel. |
| O que você digita aparece embaralhado | Logs invadindo o terminal de input | Redirecione os logs: `python3 main.py 2> no.log` (veja §5). |
| `Address already in use` na porta 6000 | Outro processo usando a 6000 | `lsof -i :6000` para achar e encerrar. |
| Máquinas não se enxergam | Broadcast/porta bloqueados ou sub-redes diferentes | Mesma sub-rede; libere UDP 6000 no firewall; teste com `ping`. |

---

## 11. Avaliação — onde cada critério está no código

| # | Critério | Onde |
|---|----------|------|
| 1 | Inicialização do anel | `node._discover`, `ring.sorted_nicknames`, `node._become_controller` |
| 2 | Funcionamento básico | `node._handle_token`, `node._handle_data`, `queue_manager.MessageQueue` |
| 3 | CRC32 | `crc.py`, verificação em `node._handle_data` (ACK/NAK/maquinainexistente) |
| 4 | Injeção de falhas | `faults.maybe_corrupt`, retransmissão em `node._process_as_origin` |
| 5 | Controle do token | `token_controller.py`, `node._on_token_lost/_on_token_duplicate` |
| 6 | Alteração topológica | `node._schedule_heartbeat/_schedule_prune`, `ring.prune_dead` |
| 7 | Observabilidade | logs INFO/WARNING; dica de `tail -f` em §5 |
