/* Scroll Cinema — a animação obedece o dedo do visitante.
   ---------------------------------------------------------------
   O vídeo não toca sozinho: cada quadro dele está amarrado a uma posição
   de rolagem. Sobe, a animação volta. Desce, avança.

   Uma variável só (`progresso`, de 0 a 1) comanda a cena inteira — vídeo,
   texto, contador, barra. Quem quiser plugar mais coisa lê o mesmo número.

   Armadilhas tratadas aqui, todas já observadas em produção:
   - `loadedmetadata` pode disparar ANTES deste script rodar (servidor
     local, vídeo em cache). Por isso lemos `readyState` no bind também;
     confiar só no evento deixa a duração em 0 para sempre e o efeito
     parece morto sem estar.
   - Painel de preview embutido não entrega `requestAnimationFrame`. Com
     a aba oculta, aplicamos direto em vez de agendar.
   - `prefers-reduced-motion` NUNCA pode zerar a altura da seção: isso
     zera o curso de rolagem, o progresso trava em 0 e a página parece
     quebrada. Corta-se o que se move sozinho, não a estrutura.
   - Sem tela de carregamento o visitante rola, não vê nada e conclui que
     o site está quebrado. Ela é obrigatória.
*/

(() => {
  "use strict";

  const ESPERA_MAXIMA = 6000; // rede lenta: mostra assim mesmo

  /* Faz o vídeo tocar por um instante para acordar o decodificador.

     O iOS não desenha quadro nenhum a partir de `currentTime` enquanto o
     vídeo não tiver tocado ao menos uma vez. Sem isto o efeito inteiro cai
     no iPhone: a rolagem move a legenda, o contador e a barra, e a imagem
     fica congelada no pôster -- o que parece bug de site, não economia.

     Tocar é seguro porque o elemento é `muted` e `playsinline`: o iOS libera
     autoplay inline nessa combinação, e o `pause()` vem no mesmo instante,
     antes de qualquer movimento visível.

     Se ainda assim o navegador recusar, refazemos no primeiro toque. Aí o
     gesto do visitante autoriza, e ele nem percebe que houve uma segunda
     tentativa -- o toque que destrava costuma ser o próprio começo da
     rolagem. */
  function prepararDecodificador(video) {
    const tentar = () => {
      const p = video.play();
      if (p && typeof p.then === "function") {
        return p.then(() => video.pause());
      }
      video.pause();
      return Promise.resolve();
    };

    tentar().catch(() => {
      const destravar = () => {
        tentar().catch(() => {});
      };
      addEventListener("touchstart", destravar, { once: true, passive: true });
      addEventListener("click", destravar, { once: true });
    });
  }

  function ligarCena(secao) {
    const video = secao.querySelector("[data-sc-video]");
    const carregando = secao.querySelector("[data-sc-carregando]");
    if (!video) return;

    let duracao = 0;
    let pronto = false;
    let alvo = 0;
    let agendado = false;

    // Estado atual E evento: ver comentário do topo.
    if (video.readyState >= 1 && video.duration) duracao = video.duration;

    const ouvintes = [...secao.querySelectorAll("[data-sc-progresso]")].map((el) => ({
      el,
      tipo: el.dataset.scProgresso,
      max: Number(el.dataset.scMax || 100),
    }));
    const legendas = [...secao.querySelectorAll("[data-sc-legenda]")];

    function aplicar() {
      agendado = false;
      if (duracao) {
        // `currentTime` só salta liso entre keyframes. O arquivo precisa
        // ter sido reencodado com keyint=1 — ver scripts/scroll_cinema.sh.
        video.currentTime = alvo * duracao;
      }

      for (const { el, tipo, max } of ouvintes) {
        if (tipo === "contador") el.textContent = Math.round(alvo * max).toLocaleString("pt-BR");
        else if (tipo === "barra") el.style.transform = `scaleX(${alvo})`;
        else if (tipo === "altura") el.style.height = `${alvo * 100}%`;
        else if (tipo === "giro") el.style.transform = `rotate(${alvo * 270 - 135}deg)`;
      }

      if (legendas.length) {
        // Divide o curso em faixas iguais, uma por legenda.
        const atual = Math.min(legendas.length - 1, Math.floor(alvo * legendas.length));
        legendas.forEach((el, i) => el.classList.toggle("ativa", i === atual));
      }
    }

    function aoRolar() {
      const caixa = secao.getBoundingClientRect();
      const curso = secao.offsetHeight - innerHeight;
      const bruto = curso > 0 ? -caixa.top / curso : 0;
      alvo = Math.min(1, Math.max(0, bruto));

      if (document.hidden) aplicar();
      else if (!agendado) {
        agendado = true;
        requestAnimationFrame(aplicar);
      }
    }

    function liberar() {
      if (pronto) return;
      pronto = true;
      duracao = video.duration || 0;
      video.classList.add("pronto");
      carregando?.classList.add("saiu");
      aoRolar();
    }

    video.addEventListener("loadedmetadata", () => {
      duracao = video.duration;
    });
    video.addEventListener("canplaythrough", liberar);
    /* `loadeddata` além de `canplaythrough`, e é o que salva o iPhone.

       O Safari do iOS não busca o arquivo inteiro sem um gesto: ele para
       assim que tem quadro suficiente e `canplaythrough` simplesmente nunca
       chega. Só com aquele evento, a cena ficava presa no véu de carregamento
       até o prazo de 6s estourar -- e aí `liberar()` rodava com `duration`
       ainda indefinida, `aplicar()` pulava o `currentTime`, e o visitante
       via o pôster parado enquanto rolava.

       `loadeddata` dispara cedo e em todo navegador. Os filmes de produto já
       usavam ele (ver scroll.js) e por isso funcionavam no mesmo iPhone em
       que este aqui não funcionava. */
    video.addEventListener("loadeddata", liberar);

    /* O download começa aqui, e só se valer a pena.

       São 12MB. Em 4G, ou com economia de dados ligada, o visitante pagaria
       franquia e ficaria segundos olhando um spinner antes da primeira frase.
       Sem filme, `duracao` fica 0, `aplicar()` não escreve `currentTime`, e o
       pôster que o elemento já pinta continua no lugar — legenda, contador e
       barra seguem obedecendo a rolagem igual. */
    const fonte = video.dataset.fonte;
    if (fonte && !(window.RedeCara && window.RedeCara())) {
      video.preload = "auto";
      video.src = fonte;
      // Trocar `preload` depois que o elemento nasceu com "none" não faz o
      // iOS buscar nada. `load()` é o que realmente inicia o download lá.
      video.load();
      prepararDecodificador(video);
      // Se o arquivo for grande demais ou a rede cair, mostra do jeito que der.
      setTimeout(liberar, ESPERA_MAXIMA);
      if (video.readyState >= 4) liberar();
    } else {
      // Nada a esperar: libera na hora para o véu de carregamento sair.
      if (fonte && window.RedeExplica) window.RedeExplica("filme de abertura");
      liberar();
    }

    addEventListener("scroll", aoRolar, { passive: true });
    addEventListener("resize", aoRolar);
    aoRolar();
  }

  document.querySelectorAll("[data-sc-cena]").forEach(ligarCena);
})();
