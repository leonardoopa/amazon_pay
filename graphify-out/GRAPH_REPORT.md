# Graph Report - amazon_pay  (2026-10-17)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 3569 nodes · 8066 edges · 163 communities (117 shown, 46 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 662 edges (avg confidence: 0.9)
- Token cost: 106,687 input · 2,243 output

## Graph Freshness
- Built from commit: `5de88bcc`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- App Config and Server Startup
- CLI Commands
- ML Showcase Offer Scoring
- HTTP API Endpoints
- Post Queue Database
- ML Coupon Panel Scraper
- Continuous Delivery Tests
- Offer Scoring and Cooldown
- Django Amazon Offer Admin
- Message Price Parsing
- Django Forms and Tests
- Repost Without Baseline
- Django Admin and URLs
- Pelando Group Links
- ML Landing Pages
- Product Recheck Collection
- Cinema Section Tests
- Coupon Selection per Offer
- Hot Product Discovery
- Amazon Group Offers
- Django Security Settings
- Affiliate Disclosure Enforcement
- Pelando Coupon Scraping
- Fast Priority Source Cycle
- Priority Theme Slots
- ML Catalog Collection
- Drip Delivery
- Post Creation and Families
- Pipeline Run and Rules
- Category Cooldown
- Offer History and Pending Posts
- Queue Drain Before Collect
- Amazon Add Command
- Quiet Hours Window
- Cheap Price Focus
- Exclusion List
- Gemini Copywriter
- Destination Group Filters
- Group Theme Matching
- Showcase Category Filters
- Repost Cap per Run
- Duplicate Listing Detection
- Same Product Matching
- Watchlist Slicing Rotation
- Priority Source Tests
- Site Savings Counters
- Coupon Loading and Expiry
- Amazon ASIN Extraction
- Pelando Deal Parsing
- Coupon Category Rules
- Affiliate Link Builder Tests
- Best Seller Categories
- Destination Group Config
- Product Page Price Parsing
- ML Offers Showcase Client
- Watchlist Validation
- Group Invite Commands
- Food and Drink Priority
- Coupon Price Calculation
- Source Group Message Links
- ML OAuth Callback
- Priority Theme Watchlist
- Official Store Selection
- API Tests
- Evolution WhatsApp Client
- Competitor Coupon Extraction
- Thematic Group Search
- Fixed Showcase Tests
- Offer Models and Sources
- Daemon Loop and Drip
- Group Invite Signature
- Product Repeat Cooldown
- Watchlist Price Caps
- Group Queue Rotation
- Price Curve SVG
- Evolution Delivery Tests
- Category API Handler Tests
- Login Rate Limit Tests
- Amazon Admin Screen
- Repeated Headline Guard
- Batch Delivery
- Queue Ordering Criteria
- Coupon Ranking Order
- Scroll Video Binding Tests
- Site Page Tests
- Gemini Rate Limit Retry
- Affiliate Panel Token
- Food and Drink Detection
- Fallback Copy and Disclosure
- Per-Theme Discount Rules
- Amazon Photo Check
- Group Member Sync
- Pelando Link Resolver
- Shared Test Fixtures
- Group Priority Themes
- Prompt Facts and Store Name
- WhatsApp Cloud API
- Amazon Affiliate Link
- Affiliate Link Persistence
- ML Link Builder Client
- Daemon Loop Timing
- Security Headers Tests
- Food Theme Prioritization
- ML Showcase Collection
- Unfounded Claims Guard
- Amazon Offers With Photos
- Brand Coupon Restriction Tests
- General Group Fan-out
- BRL Price Formatting Guard
- Deal Page Parsing
- WhatsApp Image Fallback
- Import Resolution Tests
- Coupon Eligibility Rules
- Video On Costly Networks
- Django Migrations
- Email Signup Rate Limit
- Link Coupon Pairing
- Security Middleware
- Meta Tags Context
- Title Enforcement
- Priority Slot Reservation
- Shared Persistent Cache
- Join Redirect View
- Config Exception
- Price Card Search
- Database Router
- HTTPS Redirect Tests
- Source Groups Watchlist
- Evolution Broken DNS Fake
- Evolution Offline Fake
- Scroll Cinema Script
- HTML Cache Revalidation
- Robots And Sitemap
- Slug To Title
- Django Manage Entry
- ML Token Refresh Lock
- Base64 Pixel Rejection
- Vitrine App Config
- Priority Showcase Pages
- Fake Gemini Models
- ML Social Hints
- API Forbidden ID Handling
- Scroll Sequence Script
- SQLite Backup Script
- VPS Setup Script
- Coupon Price Fixture
- Dead Session Memoization
- Reservation Fixture
- Showcase Origin Stamp
- Hint Without Photo
- Female Perfume Filter
- Arbo Brand Matching
- Body Splash Theme
- Scroll Cinema Shell
- Promo

## God Nodes (most connected - your core abstractions)
1. `Offer` - 161 edges
2. `run()` - 68 edges
3. `record_offer()` - 68 edges
4. `ScoredOffer` - 66 edges
5. `cupom_para()` - 52 edges
6. `connect()` - 49 edges
7. `conn()` - 47 edges
8. `now()` - 40 edges
9. `MercadoLivre` - 39 edges
10. `create_post()` - 39 edges

## Surprising Connections (you probably didn't know these)
- `drena()` --calls--> `MissingConfig`  [INFERRED]
  tests/test_fila_antes_da_coleta.py → src/promo/config.py
- `test_grupo_sem_cota_propria_usa_a_geral()` --uses--> `GrupoDestino`  [INFERRED]
  tests/test_landing_pages.py → src/promo/pipeline.py
- `test_o_padrao_e_de_tres_minutos()` --calls--> `fonte_rapida_intervalo_segundos()`  [INFERRED]
  tests/test_ciclo_rapido.py → src/promo/config.py
- `test_o_freio_tem_um_valor_padrao()` --calls--> `max_fonte_prioritaria_por_run()`  [INFERRED]
  tests/test_fonte_prioritaria.py → src/promo/config.py
- `test_backend_invalido_falha_com_nome()` --calls--> `build_delivery()`  [INFERRED]
  tests/test_evolution.py → src/promo/delivery/__init__.py

## Import Cycles
- None detected.

## Communities (163 total, 46 thin omitted)

### Community 0 - "App Config and Server Startup"
Cohesion: 0.03
Nodes (56): cmd_serve(), _evolution_sem_grupo(), api_host(), api_port(), api_secret(), api_worker(), category_cooldown_minutes(), copy_model() (+48 more)

### Community 1 - "CLI Commands"
Cohesion: 0.04
Nodes (29): cmd_cupons(), cmd_flush(), cmd_init(), cmd_ml_auth(), cmd_ml_categories(), cmd_ml_probe(), cmd_run(), cmd_test_whatsapp() (+21 more)

### Community 2 - "ML Showcase Offer Scoring"
Cohesion: 0.05
Nodes (47): preenche(), score_campaign(), _componente(), _frete_gratis(), _imagem(), _offer(), _preco(), card() (+39 more)

### Community 3 - "HTTP API Endpoints"
Cohesion: 0.05
Nodes (37): create_app(), amazon_add(), estado(), health(), _guarded_run(), lifespan(), OfertaAmazon, require_key() (+29 more)

### Community 4 - "Post Queue Database"
Cohesion: 0.06
Nodes (31): cmd_link_all(), AmazonConfig, post_max_age_minutes(), affiliate_blocked(), affiliate_link(), enviados_na_janela(), expire_stale_posts(), families_in_cooldown() (+23 more)

### Community 5 - "ML Coupon Panel Scraper"
Cohesion: 0.06
Nodes (36): Cupom, _cupons_do_html(), enriquecer_com_temas(), nomes_das_categorias(), normalizar(), _pagina(), sem_acento_simples(), _tema_da_palavra() (+28 more)

### Community 6 - "Continuous Delivery Tests"
Cohesion: 0.07
Nodes (39): agora(), ambiente(), banco(), enfileirar(), EntregaFalsa, ja_enviado(), status(), test_a_rodada_so_enfileira_quando_a_thread_esta_ligada() (+31 more)

### Community 7 - "Offer Scoring and Cooldown"
Cohesion: 0.08
Nodes (36): _in_cooldown(), score(), score_pista(), make_conn(), oferta(), postar(), test_a_bicicleta_ergometrica_nao_sai_quatro_dias_seguidos(), test_fora_da_janela_libera_mesmo_com_o_preco_igual() (+28 more)

### Community 8 - "Django Amazon Offer Admin"
Cohesion: 0.05
Nodes (25): OfertaAmazonForm, HistoricoPreco, Meta, Post, Meta, Produto, Meta, amazon_add() (+17 more)

### Community 9 - "Message Price Parsing"
Cohesion: 0.06
Nodes (32): _reject_preco_sem_condicao(), _condicoes_da_mensagem(), oferta_da_mensagem(), OfertaAmazon, _para_float(), _precos_da_mensagem(), _titulo_da_mensagem(), _scored() (+24 more)

### Community 10 - "Django Forms and Tests"
Cohesion: 0.06
Nodes (9): InscricaoForm, Meta, Inscrito, Meta, CacheDosNumerosTests, FormatacaoDeNumeroTests, IscaTests, LimiteDeEnvioTests (+1 more)

### Community 11 - "Repost Without Baseline"
Cohesion: 0.06
Nodes (27): banco(), enfileirar(), oferta(), pontuada_sem_medicao(), score_pista(), test_a_espera_cresce_entre_as_tentativas(), test_a_pista_carrega_a_foto_do_anuncio(), test_a_pista_inedita_passa_pelo_cooldown() (+19 more)

### Community 12 - "Django Admin and URLs"
Cohesion: 0.06
Nodes (11): CliqueAdmin, GrupoAdmin, InscritoAdmin, Command, Clique, Meta, Grupo, Meta (+3 more)

### Community 13 - "Pelando Group Links"
Cohesion: 0.09
Nodes (35): links_do_pelando(), ofertas_do_grupo(), cache_limpo(), com_url(), instala(), MLFalso, pelando(), pelando_com() (+27 more)

### Community 14 - "ML Landing Pages"
Cohesion: 0.07
Nodes (27): collect_landings(), load_landing_pages(), CatalogoFalso, de(), _grupo(), oferta(), test_a_cota_maior_dos_dois_grupos(), test_a_maquiagem_da_vitrine_e_prioridade_em_mulheres() (+19 more)

### Community 15 - "Product Recheck Collection"
Cohesion: 0.09
Nodes (25): tracked_products(), _best_image(), _as_ctx, FakeSource, ids(), make_conn(), make_offer(), Rules (+17 more)

### Community 16 - "Cinema Section Tests"
Cohesion: 0.05
Nodes (6): CenaTests, CssTests, EsteiraTests, FilmeDoProdutoTests, VitrineFixadaTests, _video_do_produto()

### Community 17 - "Coupon Selection per Offer"
Cohesion: 0.07
Nodes (28): cupom_para(), geral(), oferta(), test_a_alternativa_que_nao_serve_fica_de_fora(), test_a_escolha_respeita_o_preco_do_produto(), test_a_mesma_oferta_no_ml_leva_o_cupom(), test_abaixo_do_minimo_nao_cita_o_cupom(), test_cupom_abaixo_do_minimo_nao_muda_o_preco() (+20 more)

### Community 18 - "Hot Product Discovery"
Cohesion: 0.10
Nodes (24): hot_products(), recent_headlines(), add_history(), add_post(), FonteQueFalha, FonteSoVitrine, make_conn(), test_a_razao_ainda_manda_quando_nao_ha_empate() (+16 more)

### Community 19 - "Amazon Group Offers"
Cohesion: 0.09
Nodes (13): asin_do_link(), imagem(), ler_mensagens(), Pista, pistas(), produto_do_link(), buscar(), CupomPelando (+5 more)

### Community 20 - "Django Security Settings"
Cohesion: 0.07
Nodes (7): chave_secreta(), hosts_permitidos(), _ip_do_pedido(), LimiteDeLoginMiddleware, CredencialDeProducaoTests, PreviewDeLinkTests, _absoluta()

### Community 21 - "Affiliate Disclosure Enforcement"
Cohesion: 0.07
Nodes (24): _cola_no_link(), _enforce_disclosure(), loja_no_prompt(), test_a_divulgacao_cola_na_linha_do_link(), test_a_divulgacao_continua_presente(), test_a_divulgacao_da_amazon_sai_literal(), test_a_divulgacao_do_ml_nunca_fica_vazia(), test_amazon_atravessa_o_enforce_intacta() (+16 more)

### Community 22 - "Pelando Coupon Scraping"
Cohesion: 0.08
Nodes (25): codigo_da_pagina(), regra_do_slug(), cupom(), html_com_codigo(), test_acha_o_codigo_da_oferta(), test_cupom_de_hoje_ainda_esta_no_prazo(), test_cupom_marcado_inativo_vem_como_inativo(), test_cupom_postado_ha_muito_tempo_ja_vence() (+17 more)

### Community 23 - "Fast Priority Source Cycle"
Cohesion: 0.07
Nodes (19): FonteFalsa, limpa(), MLFalso, oferta_do_bva(), RedatorFalso, rodada_rapida(), test_a_rodada_completa_consome_o_intervalo_normalmente(), test_a_rodada_completa_le_todas_as_fontes_sem_o_ciclo_rapido() (+11 more)

### Community 24 - "Priority Theme Slots"
Cohesion: 0.10
Nodes (22): e_prioritaria(), _priorizar_temas(), oferta(), test_a_reserva_e_teto_e_nao_toma_a_rodada(), test_acento_e_caixa_nao_atrapalham(), test_desligar_o_foco_nao_afeta_o_resto_da_fila(), test_lista_de_temas_vazia_nao_muda_nada(), test_lista_vazia_nao_estoura() (+14 more)

### Community 25 - "ML Catalog Collection"
Cohesion: 0.12
Nodes (20): build(), FakeClient, FakeResponse, listing(), test_anuncio_sem_preco_e_descartado(), test_falha_na_foto_nao_derruba_a_oferta(), test_foto_que_ja_veio_nao_gasta_chamada(), test_foto_vazia_e_buscada_no_catalogo() (+12 more)

### Community 26 - "Drip Delivery"
Cohesion: 0.12
Nodes (16): banco(), enfileira(), FakeDelivery, flush(), Relogio, test_erro_pontual_nao_para_os_outros(), test_espera_entre_um_post_e_o_proximo(), test_fila_vazia_nao_envia_nada() (+8 more)

### Community 27 - "Post Creation and Families"
Cohesion: 0.10
Nodes (19): create_post(), produtos_ja_postados(), conexao_com(), test_familia_recem_postada_fica_bloqueada(), test_outra_familia_nao_e_bloqueada(), test_post_na_fila_ja_bloqueia_a_familia(), _oferta(), test_banco_antigo_ganha_a_coluna_verified() (+11 more)

### Community 28 - "Pipeline Run and Rules"
Cohesion: 0.09
Nodes (20): disparar(), rodar_e_liberar(), cmd_demo(), family_cooldown_minutes(), Rules, get_meta(), current_offers(), _cabe_no_geral() (+12 more)

### Community 29 - "Category Cooldown"
Cohesion: 0.11
Nodes (20): categories_in_cooldown(), _um_por_produto(), test_lista_vazia_nao_estoura(), make_conn(), oferta(), postar(), test_a_janela_da_categoria_e_menor_que_a_do_produto(), test_banco_sem_post_nao_bloqueia_nada() (+12 more)

### Community 30 - "Offer History and Pending Posts"
Cohesion: 0.16
Nodes (21): last_post(), pending_posts(), record_offer(), add_post(), make_conn(), make_offer(), test_desiste_depois_do_teto_de_tentativas(), test_envio_bem_sucedido_sai_da_fila() (+13 more)

### Community 31 - "Queue Drain Before Collect"
Cohesion: 0.07
Nodes (13): Relogio, rodada_instrumentada(), drena(), test_a_fila_drena_antes_da_coleta(), test_configuracao_faltando_ainda_para_o_daemon(), drena(), test_devolve_o_tempo_de_gotejamento(), test_drena_de_novo_depois_da_coleta() (+5 more)

### Community 32 - "Amazon Add Command"
Cohesion: 0.12
Nodes (20): main(), banco(), linhas(), rodar(), test_a_imagem_sai_do_asin(), test_a_tag_da_linha_de_comando_ganha_do_ambiente(), test_dry_run_nao_enfileira(), test_enfileira_o_post() (+12 more)

### Community 33 - "Quiet Hours Window"
Cohesion: 0.13
Nodes (18): em_horario_silencioso(), as_horas(), janela_padrao(), _RelogioFixo, test_horario_malformado_nao_silencia_em_silencio(), test_janela_no_mesmo_dia(), test_janela_vazia_nunca_silencia(), test_logo_apos_o_inicio_esta_dentro() (+10 more)

### Community 34 - "Cheap Price Focus"
Cohesion: 0.13
Nodes (21): _intercalar_por_fonte(), _priorizar_baratos(), oferta(), precos(), test_baratos_ficam_com_a_maior_parte_das_vagas(), test_foco_desligado_mantem_o_comportamento_antigo(), test_produto_caro_nao_e_excluido(), test_reserva_maior_abre_mais_espaco_ao_caro() (+13 more)

### Community 35 - "Exclusion List"
Cohesion: 0.12
Nodes (20): e_barrada(), load_exclude(), oferta(), test_a_lista_de_exclusao_ja_vem_normalizada(), test_a_marca_tem_precedencia_sobre_a_barra(), test_acento_e_caixa_nao_escapam_da_barra(), test_entrada_vazia_e_ignorada(), test_exclude_ausente_e_o_normal() (+12 more)

### Community 36 - "Gemini Copywriter"
Cohesion: 0.15
Nodes (21): Copywriter, FakeClient, make_scored(), post_com_chamada(), test_aceita_chamada_original(), test_aceita_loja_oficial_quando_e_verdade(), test_amazon_nao_ganha_divulgacao_de_volta_pelo_enforce(), test_chamada_parecida_mas_diferente_passa() (+13 more)

### Community 37 - "Destination Group Filters"
Cohesion: 0.14
Nodes (18): GrupoDestino, oferta(), _por_nome(), test_o_geral_nao_tem_busca_propria(), test_a_exclusao_do_grupo_nao_derruba_o_resto_da_marca(), test_a_exclusao_vence_o_tema(), test_a_mesma_oferta_vai_para_dois_grupos(), test_cueca_nao_entra_no_grupo_de_mulheres() (+10 more)

### Community 38 - "Group Theme Matching"
Cohesion: 0.10
Nodes (17): destinos(), test_a_lupo_de_vestir_esportiva_entra(), test_a_maquiagem_de_marca_entra_no_grupo_de_mulheres(), test_a_oferta_cai_nos_grupos_certos(), test_a_peca_esportiva_continua_entrando_pelo_qualificador(), test_o_arbo_nao_traz_carboidrato(), test_o_esportes_aceita_o_que_o_dono_listou(), test_o_esportes_aceita_o_recorte_de_17_09() (+9 more)

### Community 39 - "Showcase Category Filters"
Cohesion: 0.12
Nodes (18): load_vitrine_categories(), card(), fonte(), handler(), pagina(), test_as_categorias_de_esporte_apontam_para_o_grupo_de_esportes(), test_as_categorias_onde_a_marca_aparece_estao_configuradas(), test_as_categorias_que_o_dono_mandou_estao_na_vitrine() (+10 more)

### Community 40 - "Repost Cap per Run"
Cohesion: 0.12
Nodes (16): max_pistas_por_run(), _quantas_pistas_cabem(), _veio_de_outro_grupo(), limpa_vistos(), oferta(), pontuada(), test_a_fila_cheia_aperta_mais_que_o_teto(), test_a_ordem_dentro_da_faixa_e_por_desconto() (+8 more)

### Community 41 - "Duplicate Listing Detection"
Cohesion: 0.11
Nodes (18): familia_do_titulo(), escolhida(), test_acento_e_caixa_nao_separam(), test_cor_nao_separa(), test_familias_diferentes_passam_todas(), test_fica_com_o_primeiro_que_e_o_de_maior_desconto(), test_genero_nao_separa_o_mesmo_tenis(), test_modelo_diferente_continua_separado() (+10 more)

### Community 42 - "Same Product Matching"
Cohesion: 0.12
Nodes (20): mesmo_produto(), test_a_mesma_air_fryer_da_wap(), test_a_mesma_bicicleta_ergometrica(), test_a_mesma_camiseta_hering_em_dois_tamanhos(), test_a_mesma_marca_com_titulo_reescrito_e_um_produto_so(), test_aspiradores_de_marcas_diferentes_passam(), test_black_skull_e_growth_nao_se_bloqueiam(), test_cor_e_genero_nao_separam_o_mesmo_produto() (+12 more)

### Community 43 - "Watchlist Slicing Rotation"
Cohesion: 0.11
Nodes (18): fatiar_watchlist(), _rodizio(), lista(), test_a_cota_nao_usada_volta_para_o_geral(), test_a_fatia_respeita_o_tamanho_pedido(), test_a_volta_completa_cobre_todo_termo(), test_dois_cursores_independentes(), test_fatia_maior_que_a_watchlist_devolve_tudo_sem_repetir() (+10 more)

### Community 44 - "Priority Source Tests"
Cohesion: 0.14
Nodes (21): coleta(), fontes(), limpa(), MLFalso, oferta(), pontuada(), prioritaria(), test_a_coleta_marca_so_o_que_veio_da_fonte_com_prioridade() (+13 more)

### Community 45 - "Site Savings Counters"
Cohesion: 0.11
Nodes (8): ContadoresTests, DestaqueTests, EconomiaTests, _item(), PaginaDaOfertaTests, _post(), RotuloDoCardTests, _com_curva()

### Community 46 - "Coupon Loading and Expiry"
Cohesion: 0.11
Nodes (17): load_coupon(), load_coupons(), escrever(), test_bloco_ausente_nao_estoura(), test_codigo_vazio_e_ignorado(), test_cupom_geral_declarado_entra(), test_cupom_no_prazo_entra(), test_cupom_sem_data_nao_entra() (+9 more)

### Community 47 - "Amazon ASIN Extraction"
Cohesion: 0.12
Nodes (18): extrair_asin(), test_a_url_real_do_dono(), test_asin_puro(), test_asin_puro_em_minuscula(), test_busca_nao_tem_asin(), test_espaco_em_volta_nao_atrapalha(), test_gp_aw_d_do_app(), test_gp_product() (+10 more)

### Community 48 - "Pelando Deal Parsing"
Cohesion: 0.11
Nodes (19): _classifica(), _condicoes_do_deal(), DealPelando, _o_que_so_o_pelando_sabe(), _preco_sem_condicao_dizivel(), _sem_rastreio(), deal(), test_amazon_com_titulo_na_frente_do_dp() (+11 more)

### Community 49 - "Coupon Category Rules"
Cohesion: 0.11
Nodes (14): com_categoria(), test_a_categoria_manda_quando_existe(), test_a_watchlist_real_tem_o_bloco_de_cupom(), test_categoria_certa_mas_abaixo_do_minimo_nao_recebe(), test_com_categoria_o_id_decide_e_o_tema_nao_e_consultado(), test_cupom_de_categoria_sem_tema_nao_alcanca_produto_sem_categoria(), test_cupom_do_watchlist_sem_categoria_continua_por_tema(), test_em_producao_o_preco_nao_leva_o_cupom() (+6 more)

### Community 50 - "Affiliate Link Builder Tests"
Cohesion: 0.12
Nodes (13): build(), painel_com(), painel_recusando(), test_endpoint_e_o_do_painel(), test_item_com_message_e_recusa(), test_item_sem_link_e_sem_motivo_nao_e_recusa(), test_item_so_com_error_code_e_recusa(), test_lista_vazia_nao_chama_o_painel() (+5 more)

### Community 51 - "Best Seller Categories"
Cohesion: 0.14
Nodes (17): Category, collect_categories(), load_categories(), FonteComDestaques, FonteSemDestaques, oferta(), Rules_stub(), test_aplica_o_teto_de_preco_da_categoria() (+9 more)

### Community 52 - "Destination Group Config"
Cohesion: 0.09
Nodes (14): load_grupos(), escrever(), test_grupo_sem_jid_fica_de_fora(), test_o_watchlist_real_tem_os_tres_grupos_novos(), test_a_exclusao_do_watchlist_chega_no_grupo(), test_a_watchlist_real_tem_o_geral_com_jid_vazio(), test_a_watchlist_real_tem_os_quatro_grupos_nichados(), test_grupo_sem_nome_fica_de_fora() (+6 more)

### Community 53 - "Product Page Price Parsing"
Cohesion: 0.12
Nodes (16): _offer_da_pista(), _cartao_em_destaque(), _precos_do_cartao(), _pista(), test_a_pista_com_preco_vira_oferta_completa(), test_a_pista_sem_endereco_canonico_nao_vira_oferta(), test_a_pista_sem_preco_nao_vira_oferta(), test_a_pista_sem_riscado_vira_oferta_sem_de() (+8 more)

### Community 54 - "ML Offers Showcase Client"
Cohesion: 0.10
Nodes (7): MLOfertas, ParseFailed, _payload(), test_layout_mudado_falha_alto_tambem_na_categoria(), test_ignora_javascript_depois_do_json(), test_json_ilegivel_falha_alto(), test_layout_mudado_falha_alto()

### Community 55 - "Watchlist Validation"
Cohesion: 0.10
Nodes (12): termos(), test_a_reserva_ainda_discrimina(), test_as_subcategorias_de_beleza_e_saude_estao_na_lista(), test_celular_saiu_das_categorias(), test_ids_de_categoria_tem_a_forma_do_ml(), test_marcas_de_cabelo_pedidas_estao_cobertas(), test_melatonina_esta_coberta(), test_nao_entrou_termo_de_roupa_de_marca() (+4 more)

### Community 56 - "Group Invite Commands"
Cohesion: 0.11
Nodes (3): Command, GrupoTests, SincronizaONome

### Community 57 - "Food and Drink Priority"
Cohesion: 0.16
Nodes (15): _admitir_pistas(), banco(), limpa_vistos(), lote(), pontuada(), test_a_comida_ocupa_a_folga_antes_do_resto(), test_a_comida_vai_na_frente_do_resto(), test_a_faixa_2_sai_antes_da_1_que_sai_antes_da_0() (+7 more)

### Community 58 - "Coupon Price Calculation"
Cohesion: 0.11
Nodes (14): preco_com_cupom(), _quanto_economiza(), fixo(), test_amazon_nao_ganha_preco_de_cupom_do_ml(), test_cai_para_o_proximo_cupom_quando_o_melhor_estoura(), test_cupom_de_marca_nao_mexe_no_preco_de_outra_marca(), test_cupom_fixo_com_minimo_serve(), test_cupom_sem_minimo_nao_serve() (+6 more)

### Community 59 - "Source Group Message Links"
Cohesion: 0.16
Nodes (15): links_das_mensagens(), _texto_da_mensagem(), msg(), test_acha_link_direto_de_produto(), test_acha_o_encurtador_do_ml(), test_ignora_link_de_outra_loja(), test_le_legenda_de_imagem(), test_le_legenda_de_video() (+7 more)

### Community 60 - "ML OAuth Callback"
Cohesion: 0.16
Nodes (14): parse_callback(), test_aceita_redirect_igual(), test_aceita_so_a_query_string(), test_aceita_url_completa(), test_barra_final_nao_conta_como_divergencia(), test_erro_mostra_os_dois_valores(), test_erro_sem_state_nao_passa_como_sucesso(), test_ignora_espacos_da_colagem() (+6 more)

### Community 61 - "Priority Theme Watchlist"
Cohesion: 0.09
Nodes (12): load_priority(), test_as_marcas_de_mochila_pedidas_escapam(), test_as_marcas_de_salao_continuam(), test_capsula_pega_suplemento_e_esta_certo_assim(), test_mochila_nao_e_mais_tema(), test_os_temas_de_cabelo_pele_e_suplemento_continuam(), test_os_temas_de_moda_e_corrida_continuam(), test_os_temas_ja_vem_normalizados() (+4 more)

### Community 62 - "Official Store Selection"
Cohesion: 0.17
Nodes (13): anuncio(), fonte(), oferta(), test_acento_no_titulo_nao_escapa_do_tema(), test_anuncio_usado_fica_de_fora_mesmo_sendo_oficial(), test_entre_oficiais_ainda_vale_o_mais_barato(), test_no_tema_vai_a_loja_oficial_mesmo_custando_mais(), test_o_anuncio_escolhido_e_marcado_como_loja_oficial() (+5 more)

### Community 63 - "API Tests"
Cohesion: 0.10
Nodes (8): client(), _client(), test_health_aberto_e_saudavel(), test_health_nao_expoe_texto_de_erro(), test_health_nunca_rodou_nao_e_degradado(), test_health_reporta_worker_morto(), test_run_recusa_rodada_concorrente(), test_sem_segredo_configurado_endpoint_fecha()

### Community 64 - "Evolution WhatsApp Client"
Cohesion: 0.13
Nodes (3): Evolution, test_dns_fora_manda_os_bytes_em_vez_de_desistir_da_foto(), test_se_os_bytes_tambem_falharem_o_post_sai_como_texto()

### Community 65 - "Competitor Coupon Extraction"
Cohesion: 0.13
Nodes (15): cupons_da_mensagem(), test_codigo_com_numero_e_valido(), test_codigo_curto_demais_nao_e_lido(), test_codigo_sem_delimitador_nao_e_lido(), test_cupom_do_ml_nunca_sai_em_post_da_amazon(), test_le_o_cupom_entre_asteriscos_como_o_economizei_escreve(), test_le_o_cupom_entre_crases_como_o_xet_escreve(), test_le_os_dois_quando_a_mensagem_oferece_alternativa() (+7 more)

### Community 66 - "Thematic Group Search"
Cohesion: 0.11
Nodes (12): test_a_reserva_padrao_e_duas_vagas(), test_a_reserva_vem_do_env(), test_cadeirinha_de_bebe_nao_entra_no_casa(), test_o_teto_conta_o_que_ja_saiu_na_hora(), test_o_teto_do_geral_vem_do_env(), test_o_teto_nao_fica_negativo(), test_o_teto_padrao_e_cinco(), test_o_teto_vem_do_env() (+4 more)

### Community 67 - "Fixed Showcase Tests"
Cohesion: 0.15
Nodes (3): VitrineFixaTests, consulta(), _fixos()

### Community 68 - "Offer Models and Sources"
Cohesion: 0.12
Nodes (4): Offer, Source, test_catalogo_com_preco_riscado_vira_repasse(), test_repasse_sem_preco_riscado_continua_recusado()

### Community 69 - "Daemon Loop and Drip"
Cohesion: 0.12
Nodes (8): cmd_daemon(), drenar_continuamente(), _drip_gap(), tem_fonte_prioritaria(), loop(), start_drenagem(), start_fonte_rapida(), alvo()

### Community 70 - "Group Invite Signature"
Cohesion: 0.16
Nodes (12): group_invite_url(), com_convite(), make_scored(), test_a_divulgacao_continua_colada_no_link(), test_chamar_duas_vezes_nao_duplica(), test_espaco_em_branco_conta_como_desligado(), test_o_convite_entra_no_fim_do_post(), test_o_convite_vem_do_env() (+4 more)

### Community 71 - "Product Repeat Cooldown"
Cohesion: 0.24
Nodes (13): products_in_cooldown(), enfileirar(), envelhecer(), make_conn(), test_banco_sem_post_nao_bloqueia_nada(), test_enviado_antes_da_janela_libera(), test_enviado_dentro_da_janela_bloqueia(), test_expirado_nao_bloqueia() (+5 more)

### Community 72 - "Watchlist Price Caps"
Cohesion: 0.10
Nodes (11): load_watchlist(), test_o_termo_de_busca_de_celular_saiu_da_watchlist(), test_kit_body_splash_cai_no_tema_que_ja_existia(), test_os_termos_do_tema_nao_tem_teto_de_preco(), test_os_termos_fora_do_tema_mantem_teto(), test_perfume_ja_era_tema_e_continua(), test_stanley_e_tema_e_os_termos_dele_existem(), test_a_busca_nao_volta_a_estourar_o_limite_do_ml() (+3 more)

### Community 73 - "Group Queue Rotation"
Cohesion: 0.13
Nodes (12): _reveza_por_grupo(), fila(), ordem(), test_fila_vazia_nao_quebra(), test_o_grupo_com_mais_posts_leva_o_resto(), test_o_orcamento_de_uma_rodada_alcanca_todos_os_grupos(), test_o_teto_corta_a_fila_do_geral(), test_os_grupos_se_revezam_na_fila() (+4 more)

### Community 74 - "Price Curve SVG"
Cohesion: 0.13
Nodes (3): Curva, montar_curva(), CurvaTests

### Community 75 - "Evolution Delivery Tests"
Cohesion: 0.14
Nodes (10): FakeEvolution, test_backend_invalido_falha_com_nome(), test_baixar_a_foto_por_conta_propria_diz_quem_somos(), test_envia_para_o_jid_do_grupo(), test_instancia_caida_nao_vira_fallback_de_texto(), test_legenda_longa_demais_vira_texto(), test_manda_imagem_com_legenda_numa_mensagem_so(), test_midia_recusada_nao_perde_a_oferta() (+2 more)

### Community 76 - "Category API Handler Tests"
Cohesion: 0.14
Nodes (10): fonte(), roteador(), handler(), test_categoria_inexistente_devolve_vazio(), test_ignora_user_product(), test_monta_oferta_completa(), test_produto_sumido_do_catalogo_e_pulado(), test_respeita_o_teto_de_produtos() (+2 more)

### Community 79 - "Repeated Headline Guard"
Cohesion: 0.20
Nodes (12): _chave_da_chamada(), escritor_que_responde(), test_acento_nao_faz_a_chamada_ser_nova(), test_chamada_nova_passa_de_primeira(), test_chamada_repetida_pede_outra(), test_chamadas_diferentes_continuam_diferentes(), test_emoji_e_pontuacao_nao_fazem_a_chamada_ser_nova(), test_insistir_na_repeticao_levanta_e_cai_no_fallback() (+4 more)

### Community 80 - "Batch Delivery"
Cohesion: 0.12
Nodes (7): rodada(), scored(), test_deliver_aceita_lote_parcial(), test_o_lote_e_de_tres(), test_o_lote_e_pequeno_o_bastante_para_valer(), test_o_orcamento_e_um_so_para_a_rodada(), test_orcamento_estourado_nao_fica_negativo()

### Community 81 - "Queue Ordering Criteria"
Cohesion: 0.15
Nodes (11): ScoredOffer, _barrada_na_selecao(), chave_da_fila(), _de_fonte_prioritaria(), _repete_nome_e_marca(), ordem(), passa(), _separar_da_fonte_prioritaria() (+3 more)

### Community 82 - "Coupon Ranking Order"
Cohesion: 0.16
Nodes (10): ordenar_como_a_rodada(), pontuada(), test_cupom_sem_tema_prioritario_ganha_de_quem_nao_tem_cupom(), test_entre_dois_com_cupom_o_desconto_decide(), test_entre_duas_premiadas_o_desconto_decide(), test_prioritario_com_cupom_ainda_manda_no_topo(), test_prioritario_com_cupom_passa_na_frente_de_desconto_maior(), test_prioritario_sem_cupom_nao_ganha_o_topo() (+2 more)

### Community 85 - "Gemini Rate Limit Retry"
Cohesion: 0.12
Nodes (6): _reject_truncated(), _retry_delay(), test_cota_sem_retry_delay_nao_trava(), test_erro_que_nao_e_cota_nao_tem_espera(), test_espera_absurda_e_recusada(), test_le_o_retry_delay_do_erro_de_cota()

### Community 86 - "Affiliate Panel Token"
Cohesion: 0.15
Nodes (12): resposta_createLink(), test_403_renova_o_token_e_tenta_de_novo(), handler(), handler(), test_manda_o_referer_do_painel(), handler(), test_mapeia_origem_para_link_curto(), handler() (+4 more)

### Community 87 - "Food and Drink Detection"
Cohesion: 0.17
Nodes (10): e_comida_ou_bebida(), _palavras(), _prioridade_do_post(), oferta(), test_comida_de_outro_grupo_ganha_a_faixa_2(), test_comida_e_bebida_de_verdade(), test_lista_vazia_nao_marca_nada(), test_o_que_cita_a_palavra_mas_nao_e_comida_nem_bebida() (+2 more)

### Community 88 - "Fallback Copy and Disclosure"
Cohesion: 0.13
Nodes (10): disclosure_for(), fallback_copy(), sem_divulgacao(), test_amazon_sai_sem_linha_de_divulgacao(), test_fallback_inclui_cupom_quando_existe(), test_fallback_sem_cupom_nao_inventa_linha(), test_fallback_tem_disclosure_e_numeros(), test_mercadolivre_continua_com_a_divulgacao() (+2 more)

### Community 89 - "Per-Theme Discount Rules"
Cohesion: 0.21
Nodes (10): regras_do_tema(), anuncio(), regras(), test_fora_do_tema_o_piso_geral_continua(), test_o_cooldown_de_repeticao_continua_valendo_no_tema(), test_o_piso_do_tema_nao_inventa_desconto(), test_o_piso_zero_deixa_passar_o_desconto_de_9_por_cento(), test_o_tema_stanley_pega_a_garrafa_cara() (+2 more)

### Community 90 - "Amazon Photo Check"
Cohesion: 0.17
Nodes (10): amazon_responde(), responder(), oferta(), test_com_foto_na_amazon_usa_a_do_asin(), test_falha_de_rede_nao_custa_a_oferta(), test_foto_do_pelando_vale_mesmo_sem_foto_na_amazon(), test_jpeg_de_verdade_conta_como_foto(), test_mesmo_asin_so_e_consultado_uma_vez() (+2 more)

### Community 92 - "Pelando Link Resolver"
Cohesion: 0.19
Nodes (8): deal_do_link(), cliente(), pelando_falso(), handler(), test_cada_link_e_resolvido_uma_vez(), test_destino_fora_do_pelando_nao_e_lido(), test_falha_de_rede_nao_vira_cache(), test_resolve_o_encurtador_e_le_a_pagina_sem_o_rastreio()

### Community 93 - "Shared Test Fixtures"
Cohesion: 0.18
Nodes (4): amazon_sempre_tem_foto(), banco_em_memoria(), ContextoDeConexao, sem_janela_de_silencio()

### Community 94 - "Group Priority Themes"
Cohesion: 0.13
Nodes (8): _grupo(), test_a_fila_nao_e_tema_do_esportes(), test_a_prioridade_de_mulheres_cobre_maquiagem_cabelo_e_unha(), test_a_prioridade_de_perfumes_e_a_marca_importada(), test_a_prioridade_e_subconjunto_do_tema(), test_cada_grupo_tem_a_propria_prioridade(), test_o_exige_e_so_do_perfumes(), test_o_feminino_continua_entrando_apesar_das_letras()

### Community 95 - "Prompt Facts and Store Name"
Cohesion: 0.14
Nodes (9): _facts(), store_name(), test_facts_carregam_os_numeros_reais(), test_facts_nao_pedem_divulgacao_vazia(), test_facts_omitem_flags_quando_falsas(), test_facts_sinalizam_menor_preco_e_frete(), test_fonte_desconhecida_falha_alto(), test_loja_bate_com_a_divulgacao() (+1 more)

### Community 97 - "Amazon Affiliate Link"
Cohesion: 0.14
Nodes (6): AmazonDoGrupo, nosso_link(), link_de_afiliado(), test_o_nosso_link_e_canonico_e_leva_a_nossa_tag(), test_a_tag_da_loja_tambem_serve(), test_o_link_leva_a_tag()

### Community 98 - "Affiliate Link Persistence"
Cohesion: 0.23
Nodes (8): config(), offer(), test_link_gerado_fica_salvo_no_banco(), test_link_ja_salvo_nao_chama_o_painel(), test_painel_fora_do_ar_nao_derruba_a_rodada(), test_produto_recusado_nao_volta_a_consultar_o_painel(), contando(), test_sem_cookie_configurado_volta_pro_fluxo_manual()

### Community 99 - "ML Link Builder Client"
Cohesion: 0.19
Nodes (3): LinkBuilder, LinkResult, BuilderContando

### Community 100 - "Daemon Loop Timing"
Cohesion: 0.20
Nodes (6): rodar(), test_desconta_o_tempo_gasto_na_rodada(), test_o_ciclo_se_mantem_estavel_entre_rodadas(), test_rodada_curta_dorme_quase_o_intervalo_inteiro(), test_rodada_instantanea_tambem_respeita_o_piso(), test_rodada_que_estoura_o_intervalo_respeita_o_piso()

### Community 102 - "Food Theme Prioritization"
Cohesion: 0.15
Nodes (6): sem_acento(), tem_tema(), _tokens_do_produto(), load_comida_e_bebida(), origens_por_grupo(), prioritaria_no_grupo()

### Community 103 - "ML Showcase Collection"
Cohesion: 0.17
Nodes (6): collect_vitrine(), test_fonte_sem_fetch_e_ignorada(), test_fonte_sem_o_parametro_continua_funcionando(), test_typeerror_de_dentro_do_fetch_nao_vira_coleta_pela_metade(), VitrineAntiga, VitrineQueExplode

### Community 104 - "Unfounded Claims Guard"
Cohesion: 0.17
Nodes (8): _normaliza(), _reject_unfounded_claims(), test_aceita_menor_preco_quando_e_verdade(), test_pega_variacoes_da_afirmacao(), test_recusa_menor_preco_inventado(), test_recusa_urgencia_inventada(), test_texto_honesto_passa(), test_todos_os_exemplos_do_system_estao_na_guarda()

### Community 105 - "Amazon Offers With Photos"
Cohesion: 0.18
Nodes (6): _amazon_do_grupo(), _com_foto(), _offer_da_amazon(), _pelando_do_grupo(), ofertas_do_grupo(), foto_existe()

### Community 106 - "Brand Coupon Restriction Tests"
Cohesion: 0.23
Nodes (7): cupom_restrito(), oferta_ml(), test_codigo_de_campanha_vale_para_qualquer_produto(), test_cupom_de_marca_nao_sai_em_produto_de_outra_marca(), test_cupom_nao_restrito_continua_valendo_para_tudo(), test_o_codigo_da_marca_vira_o_recorte_do_cupom(), test_o_tema_declarado_vence_o_codigo()

### Community 107 - "General Group Fan-out"
Cohesion: 0.17
Nodes (7): oferta(), scored_de(), test_a_prioridade_do_grupo_passa_na_frente(), test_o_cooldown_do_geral_continua_valendo(), test_o_mesmo_nome_recente_no_geral_tambem_barra(), test_o_que_casa_a_prioridade_do_geral_sai_no_geral(), test_o_que_nao_casa_a_prioridade_fica_so_no_grupo()

### Community 108 - "BRL Price Formatting Guard"
Cohesion: 0.18
Nodes (8): brl(), _reject_preco_de_cupom_solto(), test_formata_em_padrao_brasileiro(), test_numero_errado_na_linha_do_preco_e_recusado(), test_preco_de_cupom_com_a_expressao_passa(), test_preco_de_cupom_solto_e_recusado(), test_sem_cupom_a_guarda_nao_interfere(), test_separador_de_milhar_em_valor_alto()

### Community 109 - "Deal Page Parsing"
Cohesion: 0.18
Nodes (8): deal_da_pagina(), _desserializa(), _maior_foto(), test_a_foto_e_a_maior_variante_e_nunca_o_cartao_com_a_marca(), test_le_a_oferta_da_amazon_na_pagina_real(), test_le_a_oferta_do_ml_com_cupom_na_pagina_real(), test_oferta_encerrada_vem_inativa(), test_pagina_sem_oferta_devolve_none()

### Community 110 - "WhatsApp Image Fallback"
Cohesion: 0.18
Nodes (6): FakeWhatsApp, test_imagem_recusada_cai_para_texto(), test_janela_fechada_nao_e_mascarada_pelo_fallback(), test_manda_imagem_com_legenda_numa_mensagem_so(), test_sem_imagem_manda_texto(), test_texto_longo_demais_para_legenda_vira_texto()

### Community 111 - "Import Resolution Tests"
Cohesion: 0.24
Nodes (3): _imports_do_projeto(), test_o_pipeline_monta_a_oferta_da_amazon_sem_import_quebrado(), test_todo_import_do_projeto_resolve()

### Community 112 - "Coupon Eligibility Rules"
Cohesion: 0.20
Nodes (5): _marca_do_codigo(), _regra_incompleta(), _serve(), test_o_sufixo_do_ml_sai_antes_da_marca(), test_so_marca_conhecida_recorta()

### Community 114 - "Django Migrations"
Cohesion: 0.25
Nodes (3): Migration, Migration, Migration

### Community 115 - "Email Signup Rate Limit"
Cohesion: 0.22
Nodes (3): _excedeu_limite(), inscrever(), _ip_do_visitante()

### Community 116 - "Link Coupon Pairing"
Cohesion: 0.28
Nodes (6): links_e_cupons(), msg(), test_cupom_de_uma_mensagem_nao_vaza_para_a_outra(), test_links_das_mensagens_continua_devolvendo_so_as_urls(), test_o_cupom_fica_com_o_link_da_mesma_mensagem(), test_post_de_outra_loja_nao_produz_par()

### Community 117 - "Security Middleware"
Cohesion: 0.29
Nodes (3): _revalidar_html(), seguranca(), middleware()

### Community 119 - "Title Enforcement"
Cohesion: 0.25
Nodes (6): _enforce_title(), test_chamada_e_preservada_no_reparo(), test_nao_sobrescreve_a_linha_de_preco(), test_nao_sobrescreve_o_link(), test_titulo_correto_passa_intacto(), test_titulo_encurtado_e_restaurado()

### Community 120 - "Priority Slot Reservation"
Cohesion: 0.25
Nodes (5): outra(), test_a_reserva_garante_vaga_para_a_prioridade(), marca(), test_sem_reserva_a_prioridade_perde_a_vaga(), marca()

### Community 124 - "Price Card Search"
Cohesion: 0.33
Nodes (5): _cards_com_preco(), card(), test_acha_card_com_preco_em_qualquer_profundidade(), test_ignora_card_sem_preco(), test_nao_entra_em_recursao_infinita()

### Community 127 - "Source Groups Watchlist"
Cohesion: 0.33
Nodes (4): load_grupos_fonte(), test_o_watchlist_real_marca_o_bva_como_prioritario(), test_fonte_sem_jid_fica_de_fora(), test_o_watchlist_real_tem_os_grupos_fonte()

### Community 130 - "Scroll Cinema Script"
Cohesion: 0.53
Nodes (5): ligarCena(), aoRolar(), aplicar(), liberar(), prepararDecodificador()

### Community 133 - "Slug To Title"
Cohesion: 0.40
Nodes (3): titulo_do_slug(), test_o_slug_vira_titulo_legivel(), test_o_titulo_do_slug_casa_tema()

### Community 135 - "ML Token Refresh Lock"
Cohesion: 0.50
Nodes (3): test_duas_threads_nao_renovam_o_token_do_ml_ao_mesmo_tempo(), conectar(), renova()

## Knowledge Gaps
- **1 isolated node(s):** `promo`
  These have ≤1 connection - possible missing edges. (Counts symbols only; 1414 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **46 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Offer` connect `Offer Models and Sources` to `App Config and Server Startup`, `CLI Commands`, `ML Showcase Offer Scoring`, `HTTP API Endpoints`, `Post Queue Database`, `Continuous Delivery Tests`, `Offer Scoring and Cooldown`, `Message Price Parsing`, `Repost Without Baseline`, `Pelando Group Links`, `ML Landing Pages`, `Product Recheck Collection`, `Coupon Selection per Offer`, `Amazon Group Offers`, `Affiliate Disclosure Enforcement`, `Fast Priority Source Cycle`, `Priority Theme Slots`, `Post Creation and Families`, `Pipeline Run and Rules`, `Category Cooldown`, `Offer History and Pending Posts`, `Cheap Price Focus`, `Exclusion List`, `Gemini Copywriter`, `Destination Group Filters`, `Repost Cap per Run`, `Duplicate Listing Detection`, `Priority Source Tests`, `Coupon Category Rules`, `Best Seller Categories`, `Destination Group Config`, `Product Page Price Parsing`, `ML Offers Showcase Client`, `Coupon Price Calculation`, `Competitor Coupon Extraction`, `Group Invite Signature`, `Product Repeat Cooldown`, `Repeated Headline Guard`, `Batch Delivery`, `Queue Ordering Criteria`, `Coupon Ranking Order`, `Food and Drink Detection`, `Per-Theme Discount Rules`, `Affiliate Link Persistence`, `Food Theme Prioritization`, `ML Showcase Collection`, `Amazon Offers With Photos`, `Brand Coupon Restriction Tests`, `General Group Fan-out`, `Coupon Eligibility Rules`, `Priority Slot Reservation`?**
  _High betweenness centrality (0.083) - this node is a cross-community bridge._
- **Are the 125 inferred relationships involving `Offer` (e.g. with `record_offer()` and `_amazon_do_grupo()`) actually correct?**
  _`Offer` has 125 INFERRED edges - model-reasoned connections that need verification._
- **What connects `promo` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `App Config and Server Startup` be split into smaller, more focused modules?**
  _Cohesion score 0.031853925096628016 - nodes in this community are weakly interconnected._
- **Why does `Grupo` connect `Django Admin and URLs` to `Django Amazon Offer Admin`, `Django Forms and Tests`, `Site Page Tests`, `Group Invite Commands`, `Join Redirect View`, `Group Member Sync`?**
  _High betweenness centrality (0.036) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `run()` (e.g. with `MissingConfig` and `Rules`) actually correct?**
  _`run()` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Should `CLI Commands` be split into smaller, more focused modules?**
  _Cohesion score 0.04187946884576098 - nodes in this community are weakly interconnected._