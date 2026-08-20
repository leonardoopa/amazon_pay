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

  /* Antes trocava o produto por classe: aparecia de repente, sumia de
     repente. Ficava estático entre uma troca e outra — o oposto do vídeo
     do topo, onde cada pixel obedece o dedo.

     Agora é o mesmo princípio do Scroll Cinema: um progresso contínuo de
     0 a 1 sobre a seção inteira, convertido em índice fracionário. O
     produto 1,4 significa "40% do caminho entre o primeiro e o segundo",
     e os dois existem ao mesmo tempo, um entrando e o outro saindo. */

  const vitrine = document.querySelector("[data-vitrine]");
  if (vitrine && !menosMovimento) {
    const itens = [...vitrine.querySelectorAll("[data-vitrine-item]")];
    const pontos = [...vitrine.querySelectorAll("[data-ponto]")];

    if (itens.length) {
      document.documentElement.classList.add("js-vitrine");

      let agendado = false;

      const desenhar = (posicao) => {
        itens.forEach((el, i) => {
          const distancia = posicao - i;
          const bruta = Math.abs(distancia);
          // Fora da vizinhança imediata o item não participa: mantê-lo
          // pintado custa composição e não aparece na tela.
          if (bruta >= 1) {
            el.style.opacity = "0";
            el.style.visibility = "hidden";
            return;
          }
          el.style.visibility = "visible";
          el.style.opacity = String(1 - bruta);
          el.style.transform =
            `translate3d(0, ${distancia * -46}px, 0) scale(${1 - bruta * 0.07})`;
        });

        const perto = Math.round(posicao);
        pontos.forEach((el, i) => el.classList.toggle("ativo", i === perto));
      };

      const medir = () => {
        agendado = false;
        const caixa = vitrine.getBoundingClientRect();
        const curso = vitrine.offsetHeight - innerHeight;
        const bruto = curso > 0 ? -caixa.top / curso : 0;
        const p = Math.min(1, Math.max(0, bruto));
        desenhar(p * (itens.length - 1));
      };

      const aoRolar = () => {
        // Aba oculta não entrega requestAnimationFrame; aplica direto.
        if (document.hidden) medir();
        else if (!agendado) {
          agendado = true;
          requestAnimationFrame(medir);
        }
      };

      addEventListener("scroll", aoRolar, { passive: true });
      addEventListener("resize", aoRolar);
      medir();
    }
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
