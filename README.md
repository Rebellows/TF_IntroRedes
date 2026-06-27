# Rede em Anel com Token (UDP) — Node.js

Implementação do trabalho de Fundamentos de Redes de Computadores: simulação
de rede local em anel com passagem de token, usando UDP puro (módulo `dgram`
nativo do Node.js — sem dependências externas).

## Requisitos

- Node.js 18+ instalado em cada máquina (sem necessidade de `npm install`,
  não há dependências externas).

## Estrutura

```
ring_network/
├── main.js              ponto de entrada (CLI)
├── config.example.json  modelo de configuração
└── src/
    ├── crc32.js          CRC32/ISO-HDLC (compatível com binascii.crc32/zlib)
    ├── protocol.js       montagem e parsing dos pacotes do protocolo
    ├── netinfo.js        detecção do IP IPv4 local da máquina
    └── RingMachine.js    núcleo: estado do anel, token, fila, heartbeat
```

## Configuração

Copie `config.example.json` para `config.json` em cada máquina e ajuste:

```json
{
  "apelido": "A",
  "ip": "192.168.0.10",
  "broadcastAddr": "255.255.255.255",
  "porta": 6000,
  "delayToken": 1000,
  "delayDados": 500,
  "probabilidadeErro": 0.1,
  "timeoutToken": 8000,
  "tempoMinimoToken": 500,
  "debug": false
}
```

- `apelido`: letra única (A, B, C...). Deve ser diferente em cada máquina.
- `ip` *(opcional)*: força o IP da máquina em vez da detecção automática.
  Útil quando a máquina tem múltiplos adaptadores de rede (ex.: VirtualBox,
  VMware) e o IP detectado automaticamente é o errado.
- `broadcastAddr`: endereço de broadcast da sub-rede. Use `255.255.255.255`
  para broadcast genérico, ou o broadcast específico da sub-rede
  (ex.: `192.168.1.255`) se a rede não repassar o broadcast geral.
- `delayToken` / `delayDados`: atraso artificial (ms) antes de passar o
  token / enviar dados, para visualizar a circulação mais devagar.
- `probabilidadeErro`: 0.0 a 1.0 — chance de corromper a mensagem antes do
  envio (a máquina recalcula o CRC sobre o conteúdo já corrompido, conforme
  especificação: "o CRC é sempre inserido após a possível corrupção").
- `timeoutToken`: tempo (ms) que a controladora aguarda sem ver o token
  passar antes de gerar um novo.
- `tempoMinimoToken`: intervalo mínimo (ms) entre passagens do token pela
  controladora; abaixo disso, é tratado como token duplicado e descartado.
- `debug` *(opcional)*: `true` para ativar logs detalhados (equivalente a
  passar `--debug` na linha de comando). Padrão: `false`.

## Execução em múltiplas máquinas reais

Em **cada PC** da mesma rede local (mesmo segmento de broadcast, ou seja,
mesmo roteador/switch sem isolamento de cliente Wi-Fi ativo):

```bash
node main.js config.json
```

Ou passando o apelido direto na linha de comando (sobrescreve o do JSON):

```bash
node main.js config.example.json A   # na máquina A
node main.js config.example.json B   # na máquina B
node main.js config.example.json C   # na máquina C
```

Cada instância:
1. Detecta automaticamente seu próprio IPv4 local (não-loopback), ou usa o
   `ip` fixado no config se fornecido.
2. Faz `bind` na porta UDP 6000 (mesma porta em todas as máquinas).
3. Envia `DISCOVER` em broadcast e aguarda 1s coletando `HELLO`.
4. Forma o anel em ordem alfabética de apelido e define o sucessor.
5. A máquina de menor apelido gera o token inicial e assume o papel de
   controladora.

## Firewall

UDP na porta 6000 deve estar liberado para entrada/saída em todas as
máquinas (broadcast e unicast).

**Linux:**
```bash
sudo ufw allow 6000/udp
```

**Windows (PowerShell como Administrador):**
```powershell
# Libera entrada
New-NetFirewallRule -DisplayName "Ring Token UDP 6000 IN" `
  -Direction Inbound -Protocol UDP -LocalPort 6000 -Action Allow

# Libera saída
New-NetFirewallRule -DisplayName "Ring Token UDP 6000 OUT" `
  -Direction Outbound -Protocol UDP -LocalPort 6000 -Action Allow
```

Para remover as regras após os testes:
```powershell
Remove-NetFirewallRule -DisplayName "Ring Token UDP 6000 IN"
Remove-NetFirewallRule -DisplayName "Ring Token UDP 6000 OUT"
```

Alternativamente, no Windows você pode liberar via interface gráfica:
**Painel de Controle → Firewall do Windows Defender → Configurações avançadas
→ Regras de Entrada → Nova Regra → Porta → UDP → 6000 → Permitir conexão**.

## Problemas com múltiplos adaptadores de rede (VirtualBox, VMware, etc.)

Este é o problema mais comum em máquinas de desenvolvimento. Se a máquina
tiver um adaptador virtual instalado (VirtualBox Host-Only, VMware Network
Adapter, Hyper-V, etc.), o Node.js pode detectar o IP errado automaticamente
e enviar/receber pacotes pela interface errada.

**Como identificar o problema:**

No Windows, rode `ipconfig` e procure adaptadores extras além do seu adaptador
LAN/Wi-Fi principal:

```
Ethernet adapter Ethernet:          ← adaptador real (use este IP)
   IPv4 Address: 192.168.0.58

Ethernet adapter Ethernet 3:        ← adaptador virtual (problema)
   IPv4 Address: 192.168.56.1
```

Se `netinfo.js` detectar `192.168.56.1` em vez de `192.168.0.58`, os pacotes
unicast serão enviados para a rede virtual e nunca chegarão aos outros nós.

**Sintomas típicos:**

- O nó envia tokens e dados, mas nunca recebe resposta.
- No log com `--debug`, os pacotes `[RX]` chegam de `192.168.56.x` em vez do
  IP real das outras máquinas.
- A outra máquina recebe seus pacotes mas você não recebe os dela
  (assimetria).

**Solução: forçar o IP correto no config:**

```json
{
  "apelido": "B",
  "ip": "192.168.0.58",
  ...
}
```

Com `"ip"` definido, o nó usa esse endereço nos pacotes HELLO e DISCOVER em
vez do detectado automaticamente. As outras máquinas armazenam esse IP para
unicast e os pacotes chegam na interface correta.

**Alternativa: desabilitar o adaptador virtual temporariamente**

No Windows (PowerShell como Administrador):
```powershell
# Verificar o nome exato do adaptador
Get-NetAdapter | Select-Object Name, InterfaceDescription, Status

# Desabilitar
Disable-NetAdapter -Name "Ethernet 3" -Confirm:$false

# Reabilitar após os testes
Enable-NetAdapter -Name "Ethernet 3"
```

> **Nota:** desabilitar o adaptador virtual pode afetar VMs em execução.
> Prefira usar o campo `"ip"` no config quando possível.

## Interagindo via terminal (stdin)

Com o processo rodando, digite no terminal:

```
enviar B Oi, tudo bem?
```

Enfileira a mensagem `"Oi, tudo bem?"` com destino ao apelido `B`
(máximo 10 mensagens na fila, FIFO).

```
status
```

Mostra hosts conhecidos, sucessor atual, se é controladora do token, se
possui o token agora e o conteúdo da fila de saída.

## Modo debug (observabilidade)

Passe `--debug` na linha de comando para ativar logs detalhados:

```bash
node main.js config.json --debug
```

Com `--debug` ativado, o nó exibe:

- `[TX]` / `[RX]` — todo pacote enviado e recebido no wire, com tipo,
  endereço e conteúdo bruto. Formato idêntico ao da implementação Python
  para facilitar comparação lado a lado.
- `[DEBUG]` — eventos internos: token recebido, token repassado (com IP
  do sucessor).
- `[REDE]` — linha por pacote de dados que passa pelo nó, mostrando
  origem, destino, flag, seq, TTL, status do CRC e conteúdo da mensagem.
  Também idêntico ao formato Python.

Sem `--debug`, apenas os eventos relevantes para o usuário são exibidos
(mensagens recebidas, ACK/NAK, erros, reconfiguração do anel).

### Filtrar HELLO e DISCOVER do log (reduzir ruído)

Em redes com múltiplos adaptadores ou durante a fase de descoberta, os
pacotes HELLO e DISCOVER podem dominar o log. Para ver apenas TOKEN,
DADOS e eventos internos:

**Linux / macOS:**
```bash
node main.js config.json --debug | grep -v "HELLO\|DISCOVER"
```

**Windows (PowerShell):**
```powershell
node main.js config.json --debug 2>&1 | Select-String -NotMatch "HELLO|DISCOVER"
```

### Exemplo de saída com `--debug` (filtrado)

```
[18:54:57.424] [B] [RX] <-- 192.168.0.26:6000   TOKEN    1000
[18:54:57.425] [B] [DEBUG] Token recebido.
[18:54:57.927] [B] [falha simulada] Mensagem corrompida antes do envio.
[18:54:57.927] [B] Enviando DADOS seq=0 destino=A via A.
[18:54:57.927] [B] [TX] --> 192.168.0.26:6000   DADOS    2000:B:A:maquinainexistente:0:4:ola:2716127284
[18:54:58.106] [B] [RX] <-- 192.168.0.26:6000   DADOS    2000:B:A:ACK:0:4:ola:1186861673
[REDE] B -> A | flag=ACK seq=0 ttl=4 | CRC ok | "ola"
[18:54:58.107] [B] ACK recebido para seq=0 (destino=A). Mensagem entregue.
[18:54:59.111] [B] [DEBUG] Token repassado para A (192.168.0.26).
[18:54:59.111] [B] [TX] --> 192.168.0.26:6000   TOKEN    1000
```

## Testando em um único PC (múltiplos terminais)

Por padrão, todas as instâncias usam a mesma porta `6000` e fazem `bind`
com `reuseAddr`, então é possível abrir vários terminais no mesmo PC e
rodar várias instâncias simultaneamente — elas vão se descobrir via
broadcast na própria interface de rede local da máquina, exatamente como
aconteceria entre PCs diferentes.

```bash
node main.js config.example.json A
node main.js config.example.json B
node main.js config.example.json C
```

> **Atenção:** em máquinas com múltiplos adaptadores de rede (ex.: VirtualBox,
> VMware), o IP detectado automaticamente pode ser o de um adaptador virtual
> em vez do adaptador LAN real. Isso faz com que pacotes unicast sejam
> roteados para a interface errada e nunca cheguem ao destino. Nesse caso,
> force o IP correto com o campo `"ip"` no config.

## Notas de implementação (para o relatório)

- **Concorrência**: Node.js é single-threaded com event loop. Não há
  threads/locks explícitos; a "concorrência" entre recepção de pacotes,
  timers (heartbeat, timeout do token, verificação de hosts) e leitura do
  stdin é resolvida pelo próprio event loop, que serializa os callbacks.
  Isso elimina condições de corrida clássicas de multithreading, ao custo
  de qualquer operação bloqueante travar todo o processo (por isso toda
  I/O aqui é assíncrona).
- **CRC32**: implementação própria por tabela (sem depender de `zlib`),
  parametrizada exatamente como CRC32/ISO-HDLC (poly `0xEDB88320` refletido,
  seed `0xFFFFFFFF`, XOR final `0xFFFFFFFF`). Validado byte a byte contra
  `binascii.crc32` do Python.
- **Token corrompido (1000 → campos extras)**: qualquer pacote tipo `1000`
  que não seja exatamente a string `"1000"` é descartado no primeiro salto,
  nunca repassado — isso é o que intencionalmente aciona o timeout na
  controladora.
- **TTL**: a origem usa `2 × número de máquinas conhecidas`; cada salto
  intermediário decrementa 1; o destino reseta para `2 × máquinas
  conhecidas por ele` ao responder ACK/NAK.