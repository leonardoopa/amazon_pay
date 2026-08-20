/* Comportamento de scroll.
   ---------------------------------------------------------------
   O CSS já faz o revelar com `animation-timeline: view()` onde o navegador
   suporta. Este arquivo cobre três coisas que CSS não faz sozinho:

     1. fallback do revelar para navegador sem scroll-driven animations;
     2. contagem crescente dos números (precisa interpolar valor, não estilo);
     3. avanço dos passos na seção fixada.

   Tudo é enriquecimento: sem JavaScript a página continua legível e o
   conteúdo aparece — nada fica preso em `opacity: 0`. */

(() => {
  "use strict";

  const menosMovimento = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const temScrollCSS = CSS.supports("animation-timeline: view()");

  /* ---------- 1. revelar ---------- */

  // Só assume o controle se o CSS não puder fazer. A classe no <html> é o que
  // libera o estado inicial escondido — sem ela nada some, então JS quebrado
  // nunca deixa a página em branco.
  if (!temScrollCSS && !menosMovimento) {
    document.documentElement.classList.add("js-revelar");

    const observador = new IntersectionObserver(
      (entradas) => {
        for (const entrada of entradas) {
          if (!entrada.isIntersecting) continue;
          entrada.target.classList.add("visivel");
          observador.unobserve(entrada.target);
        }
      },
      { rootMargin: "0px 0px -12% 0px", threshold: 0.12 }
    );

    document.querySelectorAll("[data-revelar]").forEach((el) => observador.observe(el));
  }

  /* ---------- 2. números ---------- */

  const formatador = new Intl.NumberFormat("pt-BR");

  function contar(el) {
    const alvo = Number(el.dataset.contar);
    if (!Number.isFinite(alvo)) return;

    const prefixo = el.dataset.prefixo || "";
    if (menosMovimento) {
      el.textContent = prefixo + formatador.format(alvo);
      return;
    }

    // O valor real já veio no HTML — quem não tem JavaScript, ou tem a aba
    // em segundo plano com rAF congelado, lê o número certo. Só zeramos no
    // instante em que a animação de fato começa.
    el.textContent = prefixo + formatador.format(0);

    const duracao = 1100;
    const inicio = performance.now();

    function passo(agora) {
      const t = Math.min(1, (agora - inicio) / duracao);
      // easeOutExpo: acelera cedo e assenta no fim, sem parecer contador de posto
      const suave = t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
      el.textContent = prefixo + formatador.format(Math.round(alvo * suave));
      if (t < 1) requestAnimationFrame(passo);
    }
    requestAnimationFrame(passo);
  }

  const numeros = document.querySelectorAll("[data-contar]");
  if (numeros.length) {
    const observadorNumeros = new IntersectionObserver(
      (entradas) => {
        for (const entrada of entradas) {
          if (!entrada.isIntersecting) continue;
          contar(entrada.target);
          observadorNumeros.unobserve(entrada.target);
        }
      },
      { threshold: 0.5 }
    );
    numeros.forEach((el) => observadorNumeros.observe(el));
  }

  /* ---------- 3. sequências guiadas pela rolagem ---------- */

  /* Um único mecanismo para a vitrine e para os passos: a cada rolagem,
     descobre qual marcador está mais perto do centro da janela e ativa o
     índice correspondente.

     Foi trocado por isto no lugar de IntersectionObserver porque o IO só
     avisa quando um alvo cruza a faixa configurada. Se dois marcadores
     cruzam junto, ou se nenhum cruza (janela alta, marcador curto), a cena
     fica na anterior sem nada indicar. Medir distância ao centro sempre
     tem uma resposta, e é a mesma resposta em qualquer altura de tela. */
  function ligarSequencia(marcas, aplicar) {
    if (!marcas.length) return;
    let ultimo = -1;

    const atualizar = () => {
      const centro = innerHeight / 2;
      let melhor = 0;
      let menorDistancia = Infinity;
      marcas.forEach((marca, indice) => {
        const r = marca.getBoundingClientRect();
        const distancia = Math.abs((r.top + r.bottom) / 2 - centro);
        if (distancia < menorDistancia) {
          menorDistancia = distancia;
          melhor = indice;
        }
      });
      if (melhor !== ultimo) {
        ultimo = melhor;
        aplicar(melhor);
      }
    };

    addEventListener("scroll", atualizar, { passive: true });
    addEventListener("resize", atualizar);
    atualizar();
  }

  const palco = document.querySelector("[data-palco]");
  if (palco) {
    const passos = [...palco.querySelectorAll("[data-passo]")];
    const cenas = [...palco.querySelectorAll("[data-cena]")];
    ligarSequencia(passos, (i) => {
      passos.forEach((p, n) => p.classList.toggle("ativo", n === i));
      cenas.forEach((c, n) => c.classList.toggle("ativa", n === i));
    });
  }

  /* ---------- 4. vitrine fixada ---------- */

  const vitrine = document.querySelector("[data-vitrine]");
  if (vitrine && !menosMovimento) {
    const itens = [...vitrine.querySelectorAll("[data-vitrine-item]")];
    const pontos = [...vitrine.querySelectorAll("[data-ponto]")];
    const marcas = [...vitrine.querySelectorAll("[data-marca]")];

    // A classe entra dentro do primeiro `aplicar`, não antes dele. É ela
    // que autoriza o CSS a fixar o quadro e esconder os produtos, e só faz
    // sentido depois que existe, comprovadamente, quem os traga de volta.
    // Se a sequência nunca rodar, a seção permanece empilhada e legível em
    // vez de virar um produto seguido de telas vazias.
    ligarSequencia(marcas, (i) => {
      document.documentElement.classList.add("js-vitrine");
      itens.forEach((el, n) => el.classList.toggle("ativa", n === i));
      pontos.forEach((el, n) => el.classList.toggle("ativo", n === i));
    });
  }

  /* ---------- 5. barra de progresso ---------- */

  const barra = document.querySelector("[data-progresso]");
  if (barra && !temScrollCSS) {
    const atualizar = () => {
      const total = document.documentElement.scrollHeight - innerHeight;
      barra.style.transform = `scaleX(${total > 0 ? scrollY / total : 0})`;
    };
    addEventListener("scroll", atualizar, { passive: true });
    atualizar();
  }
})();
