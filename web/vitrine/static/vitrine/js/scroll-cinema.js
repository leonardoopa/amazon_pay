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
    // Se o arquivo for grande demais ou a rede cair, mostra do jeito que der.
    setTimeout(liberar, ESPERA_MAXIMA);
    if (video.readyState >= 4) liberar();

    addEventListener("scroll", aoRolar, { passive: true });
    addEventListener("resize", aoRolar);
    aoRolar();
  }

  document.querySelectorAll("[data-sc-cena]").forEach(ligarCena);
})();
