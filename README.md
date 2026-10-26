## Arquitetura

API em Python 3.12, FastAPI e Google ADK **2.13.0**, com pacote em `src/aurora/`.
O modelo interpreta a conversa e preenche argumentos; as tools executam as regras.

| Agente | Responsabilidade e acionamento | Motivo |
| --- | --- | --- |
| `aurora_assistant` | `LlmAgent` em modo `chat`; recebe a conversa, transfere para reservas/visitantes e consulta regulamento via `AgentTool`. | Coordena sem tools de leitura ou escrita dos dados do condomínio e sem regulamento nas instruções. |
| `reservations_specialist` | Subagente por `transfer_to_agent`; lista reservas próprias, consulta disponibilidade, reserva e cancela. | As chamadas e resultados das tools ficam nos eventos persistidos da sessão do morador. |
| `visitors_specialist` | Subagente por transferência; lista visitantes próprios e solicita autorização. | Preserva a auditoria de acesso e as confirmações na sessão do morador. |
| `regulation_specialist` | Tool do principal via `AgentTool`; chama `search_regulation` e devolve resposta breve. | O capítulo consultado fica na sessão temporária do especialista; somente a pergunta e a resposta breve da tool entram na sessão do morador. |

A árvore está em [agents.py](src/aurora/agents.py). Os especialistas de reservas e visitantes
usam `disallow_transfer_to_parent=False` e `disallow_transfer_to_peers=False`.
O roteador pode iniciar uma nova mensagem no último especialista; permitir retorno ao principal
e transferência entre pares permite tratar o próximo assunto, inclusive regulamento.
As instruções orientam essa transferência quando o assunto muda. O teste
`test_free_reservation_cancel_and_cross_specialist_routing` verifica a troca entre especialistas;
`test_regulation_agenttool_isolation` verifica a volta de visitantes ao principal para consultar piscina.
A interpretação do assunto continua dependendo do modelo; a identidade e as permissões não.
As instruções avaliam cada mensagem pelo assunto atual, sem bloquear pedidos por falas anteriores.
Todas as tools de dados atuam somente no apartamento da sessão; alegar outro apartamento não
altera esse vínculo. Para dados de outro apartamento, a resposta informa que só pode consultar
ou alterar os próprios registros. Com nome e data, visitantes chama `authorize_visitor`
imediatamente; com área e data, reservas chama `reserve_area`, e cancelamentos próprios usam
`cancel_my_reservation`. As tools decidem as regras e eventuais confirmações, sem aprovação
prévia por chat. `test_visitor_after_other_apartment_claim` verifica o fluxo offline de uma
alegação de outro apartamento seguida de autorização pendente, aprovação e listagem de visitante;
o modelo roteirizado verifica a integração, sem comprovar a interpretação do modelo real.

[service.py](src/aurora/service.py) usa `Runner` com `SqliteSessionService`
(`var/sessions.db`) e `ResumabilityConfig(is_resumable=False)`.
Essa configuração foi escolhida e validada com sessão persistida: o ADK reconhece o
`FunctionResponse`, associa seu ID à chamada anterior e roteia para o especialista autor,
sem precisar habilitar a retomada geral de agentes. `test_confirmation_after_full_restart`
fecha a aplicação, cria outro App/Runner/serviço no mesmo diretório temporário, confere todos
os eventos anteriores, aprova a pendência e verifica a gravação única e novas mensagens.
Não é uma simulação de armazenamento em memória.

Foram inspecionados no ADK instalado: `runners.py` (`run_async`, `_session_for_routing`,
`_resolve_invocation_id_from_fr`, `_setup_context_for_resumed_invocation`),
`agents/_agent_router.py` (`find_agent_to_run`), `flows/llm_flows/tools/_confirmation.py`,
`tools/tool_confirmation.py` e `tools/agent_tool.py`.
Neste último, `AgentTool.run_async` cria um Runner com `InMemorySessionService` e sessão nova;
o teste verifica que o capítulo aparece no pedido interno ao modelo especialista, mas não
nos eventos nem nos pedidos ao modelo principal.
A [documentação oficial de confirmação](https://adk.dev/tools-custom/confirmation/)
explica o protocolo; a compatibilidade desta combinação com persistência foi verificada pelos testes.

[config.py](src/aurora/config.py) carrega `.env` na inicialização e resolve os nomes dos modelos.
O cliente Gemini usa `HttpRetryOptions` com quatro tentativas, espera exponencial de 1 a 8 segundos
e repetição em 429 e 5xx. Erros permanentes ou quota esgotada ainda podem impedir uma conversa.
Em [models.py](src/aurora/models.py), `ResilientGemini.generate_content_async` repete falhas
`httpx.TransportError` até três vezes adicionais (1, 2 e 4 segundos), somente sem streaming
e antes da primeira resposta. Se o Runner ainda falhar, `ConversationService.run` em
[service.py](src/aurora/service.py) registra a exceção e retorna HTTP 200 no formato normal,
com aviso de problema temporário e pendências relidas da sessão persistida; em confirmações,
informa o registro da resposta quando o `FunctionResponse` já está persistido.
A suíte automatizada é offline e bloqueia chamadas a modelos reais.

## Garantias

1. **Cobrança ou acesso só com confirmação.** Em [tools.py](src/aurora/tools.py),
   `reserve_area` consulta a taxa do catálogo e chama `ToolSet.confirmation` para taxa positiva;
   `authorize_visitor` sempre chama essa função. Sem `tool_context.tool_confirmation`,
   o código executa `tool_context.request_confirmation(payload=...)` e retorna sem gravar.
   Com `confirmed=False`, retorna `status="negada"` e informa que o morador negou a confirmação
   e nada foi executado; somente aprovação permite escrever.
   Os detalhes incluem `area/data/taxa` ou `nome/data`. Área ocupada é recusada antes de pedir
   confirmação; quadra sem taxa grava diretamente. O resultado de sucesso inclui `taxa`,
   `cobranca` e `mensagem`: quadra retorna `cobranca=false` e "Reserva confirmada. Sem cobrança.";
   salão aprovado retorna `cobranca=true` e "Reserva confirmada. Cobrança de R$ 150,00 gerada.".
   Repetir a mesma chamada recupera o resultado armazenado com esses campos, sem nova escrita.
   As instruções só permitem mencionar confirmação no aplicativo com `status="pendente"`;
   depois do sucesso, não pedem nova confirmação. O apartamento não é argumento de tool.
   Em [service.py](src/aurora/service.py), `pending_confirmations` varre **todos** os eventos:
   cada chamada `adk_request_confirmation` sem resposta de função com o mesmo ID é uma pendência.
   A ação vem de `originalFunctionCall.name` e os detalhes de `toolConfirmation.payload`.
   `ConversationService.confirm` verifica o ID na sessão sob `asyncio.Lock` e retorna 409
   se desconhecido, de outra sessão ou já respondido. Envia ao Runner conteúdo de usuário com
   `FunctionResponse(id=..., name="adk_request_confirmation", response={"confirmed": ...})`.
   `Storage.reserve` e `Storage.authorize` também registram o resultado por sessão/chamada em
   `operations`, na mesma transação da escrita. Texto de chat não produz esse objeto de confirmação.
   Novas mensagens enquanto há pendência apenas pedem a resposta no aplicativo, sem executar o Runner.

2. **Sessão vinculada ao apartamento.** `ConversationService.create` grava `apartamento` no
   estado ADK e em `sessions`, no banco do condomínio. Em [tools.py](src/aurora/tools.py),
   `ToolSet.identity` exige igualdade entre `tool_context.state["apartamento"]` e
   `Storage.session_apartment(tool_context.session.id)`. Todas as tools de dados usam essa validação;
   nenhuma aceita apartamento como argumento. Em [storage.py](src/aurora/storage.py),
   `reservations`, `visitors` e `cancel` incluem o apartamento no `WHERE`.
   Cancelamento sem correspondência responde genericamente que o morador não possui a reserva.
   `available` consulta somente existência e a tool devolve livre/ocupada, sem dados da reserva.
   Não há tool de consulta ao cadastro de moradores. O teste de identidade adulterada verifica
   a recusa de todas as tools mesmo com estado divergente do registro.

3. **Persistência no reinício e códigos sem repetição.** [storage.py](src/aurora/storage.py)
   mantém reservas, visitantes, registro de sessões, operações e códigos em `var/aurora.db`.
   `Storage.__init__` cria o esquema e importa os JSON somente na primeira inicialização,
   controlada por `metadata`, dentro de transação. Não restaura dados ao reiniciar.
   `reserve` gera código com `secrets.token_hex`, verifica `issued_codes` e usa chaves primárias;
   cancelamento preserva a linha com `status='cancelada'`. `reset` restaura reservas e visitantes,
   preservando `issued_codes`, operações e registro das sessões. Assim, nem códigos de reservas
   canceladas ou removidas por restauração voltam a ser emitidos.
   Os eventos ADK ficam em outro arquivo SQLite e são retornados completos por
   `event.model_dump(mode="json", by_alias=True, exclude_none=True)` em [api.py](src/aurora/api.py).
   Os testes verificam reinício, códigos, colisões forçadas, restauração e persistência das alterações.
   O modelo não participa da gravação, da geração de códigos nem da recuperação dos eventos:
   tudo isso é feito por código e por SQLite, antes e depois de qualquer chamada ao modelo.

4. **Regulamento consultado por assunto.** Em [regulation.py](src/aurora/regulation.py),
   `Regulation.__init__` separa os títulos `## Capítulo`; `search` pontua palavras sem acentos,
   com peso maior para o título, e retorna **um único capítulo**. Não há regulamento no prompt
   principal. Só `regulation_specialist` possui `search_regulation`; sua sessão é isolada pelo ADK.
   Os testes cobrem piscina aos domingos (Capítulo IV, fechamento às 20h), silêncio, mudanças,
   animais e garagem, além de inspecionar os eventos e os pedidos ao modelo principal.
   O texto integral dos demais capítulos não passa pela tool nem entra no histórico do morador.
   O modelo só escolhe o texto da pergunta: a separação em capítulos, a pontuação e o limite
   de um capítulo por busca estão em código, e o principal nunca recebe o texto do regulamento.

5. **Uma reserva por área/data no instante da escrita.** Em [storage.py](src/aurora/storage.py),
   o índice `active_slot` é `UNIQUE(area, data) WHERE status = 'ativa'`.
   `Storage.reserve` usa `BEGIN IMMEDIATE` e `busy_timeout=30000`; a conferência, emissão do código,
   inserção e registro de operação ficam na mesma transação. Uma disputa/`IntegrityError` de
   ocupação vira resultado normal de data indisponível. O banco, e não a resposta do modelo,
   impede duplicação. Os testes aprovam duas pendências via HTTP com `asyncio.gather`
   (ambas recebem 200; uma única reserva ativa) e disputam a mesma data com 40 chamadas em threads.

As rotas de verificação leem diretamente o SQLite, sem chamar modelos. Elas existem para avaliação;
a autenticação é representada pelo apartamento informado na criação da sessão, conforme o escopo
do desafio. Os testes usam modelo `BaseLlm` roteirizado e bloqueiam chamadas Gemini,
com API, Runner, App, tools e persistência reais em diretórios temporários.

## Como rodar

Pré-requisitos: Python 3.12 ou superior, [uv](https://docs.astral.sh/uv/) e uma chave do
Google AI Studio para conversar com o assistente. Não requer Docker ou serviço externo.
Execute os comandos na raiz do repositório. Se `uv` não estiver no PATH, use `~/.local/bin/uv`.

```bash
uv sync
cp .env.example .env
```

Preencha o `.env` local (ignorado pelo Git):

| Variável | Uso |
| --- | --- |
| `GOOGLE_GENAI_USE_VERTEXAI` | Use `FALSE` para Google AI Studio. |
| `GOOGLE_API_KEY` | Sua chave do Google AI Studio. |
| `AURORA_MODEL` | Modelo padrão de todos os agentes; vazio usa `gemini-3.1-flash-lite`. |
| `AURORA_MODEL_MAIN` | Sobrescreve o modelo do agente principal; vazio herda o padrão. |
| `AURORA_MODEL_SPECIALISTS` | Sobrescreve o modelo dos três especialistas; vazio herda o padrão. |

**Escolha do modelo.** Na sondagem de 25/10/2026 com a chave do Google AI Studio,
`gemini-2.5-flash` e `gemini-2.5-flash-lite` retornaram 404 (indisponíveis para novos usuários).
Funcionaram `gemini-3.1-flash-lite`, `gemini-3.5-flash-lite`, `gemini-3.5-flash` e `gemini-3.8-flash`.
Os preços pagos por 1 milhão de tokens de entrada/saída consultados foram, respectivamente:
3.1-flash-lite US$ 0,25 / US$ 1,50; 3.5-flash-lite US$ 0,30 / US$ 2,50;
3.8-flash US$ 0,75 / US$ 3,75. Todos os modelos sondados disponíveis têm camada gratuita
([preços oficiais](https://ai.google.dev/gemini-api/docs/pricing)).
O padrão `gemini-3.1-flash-lite` foi escolhido pelo menor custo entre os modelos validados:
um avaliador caixa-preta via HTTP executou com ele o fluxo completo do desafio (etapas 1–14),
incluindo reinício por Ctrl+C e aprovações concorrentes. Com a versão final, foram cinco
execuções consecutivas com 51/51 verificações aprovadas, nenhuma resposta 500 e nenhum HTTP 429.
Rodadas anteriores revelaram uma recusa indevida de visitante e uma falha de transporte,
corrigidas nas instruções e em `ResilientGemini`, conforme descrito em Arquitetura.
O morador deve conferir os limites ativos do próprio projeto no Google AI Studio.

```bash
uv run aurora-reset
uv run aurora-api
```

A restauração repõe reservas e visitantes de `dados/*.json` e **mantém as sessões ADK e o
histórico de todos os códigos emitidos**, inclusive antes de restaurações anteriores.
Execute a restauração com a API parada. `dados/` é somente leitura para a aplicação.
O comando da API inicializa o banco automaticamente se ainda não houver estado inicializado.
A API responde em `http://localhost:8000`; Ctrl+C encerra. O mesmo comando reinicia preservando
o estado em `var/aurora.db` e `var/sessions.db`. Não execute a restauração entre os reinícios
se quiser conservar as alterações do fluxo. O diretório `var/` é ignorado pelo Git.

Exemplos do contrato:

```bash
curl -s http://localhost:8000/apartamentos/101/reservas
# [{"codigo":"RSV-1377","area":"quadra","data":"2030-03-09"}]
curl -s http://localhost:8000/apartamentos/302/visitantes
# [{"nome":"Marina Duarte","data":"2030-03-16"}]

curl -i -X POST http://localhost:8000/sessoes \
  -H 'Content-Type: application/json' -d '{"apartamento":"101"}'
# 201, {"session_id":"..."}
```

Copie o `session_id` retornado para `SESSION_ID` e o ID de uma confirmação pendente para `CONFIRMATION_ID`:

```bash
SESSION_ID='substitua-pelo-id-da-sessao'
curl -s -X POST "http://localhost:8000/sessoes/$SESSION_ID/mensagens" \
  -H 'Content-Type: application/json' \
  -d '{"texto":"Reserve o salão de festas para 2030-04-20"}'
# 200: {"resposta":"Confirme a reserva pelo aplicativo.","confirmacoes_pendentes":[{"id":"...","acao":"reserve_area","detalhes":{"area":"salao-de-festas","data":"2030-04-20","taxa":150.0}}]}

CONFIRMATION_ID='substitua-pelo-id-da-confirmacao'
curl -i -X POST "http://localhost:8000/sessoes/$SESSION_ID/confirmacoes" \
  -H 'Content-Type: application/json' \
  -d "{\"id\":\"$CONFIRMATION_ID\",\"confirmado\":true}"
# 200: {"resposta":"Reserva confirmada. Cobrança de R$ 150,00 gerada.","confirmacoes_pendentes":[]}
# Use false para negar; 200 no mesmo formato, informando que nada foi executado pela negativa.
# Repetir o mesmo ID ou enviar ID não pendente recebe 409.

curl -s "http://localhost:8000/sessoes/$SESSION_ID/eventos"
# 200: lista ordenada dos eventos completos.
curl -i http://localhost:8000/sessoes/sessao-inexistente/eventos
# 404
```

`resposta` é o último texto final do agente nesta execução e pode ser vazio durante uma pausa.
Os textos dos exemplos são ilustrativos; a redação final depende do modelo.
`confirmacoes_pendentes` sempre inclui todas as pendências ainda não respondidas da sessão.
Sessões inexistentes recebem 404 nas três rotas com `session_id`.
Apartamento desconhecido na criação recebe 404; rotas de verificação para apartamento desconhecido
retornam lista vazia. Datas devem usar `AAAA-MM-DD`. A API interativa do contrato fica em `/docs`.

Para executar as verificações offline (sem chave e sem chamadas externas):

```bash
uv run pytest -q
```

O modelo falso devolve transferências, chamadas de tool e textos predefinidos por agente.
A suíte inclui confirmação negada/aprovada, repetição e ID de outra sessão, autorização de
visitante mesmo com aprovação textual, quadra gratuita, cancelamento próprio, isolamento,
retomada após reinício, concorrência, busca de capítulos e respostas 404.
