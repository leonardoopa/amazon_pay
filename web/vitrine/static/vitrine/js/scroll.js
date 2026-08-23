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

    /* Cada filme sabe de qual item ele é. É o índice que define a fatia da
       rolagem que comanda o tempo dele. */
    const trilhas = [...vitrine.querySelectorAll("[data-vitrine-video]")]
      .map((video) => ({
        video,
        quadro: video.closest(".palco-com-filme"),
        indice: itens.indexOf(video.closest("[data-vitrine-item]")),
      }))
      .filter((t) => t.indice >= 0);

    /* O mesmo 861px do CSS, e não é decoração: acima dele os itens ficam
       absolutos, um por cima do outro, e apagar os de fora é o efeito.
       Abaixo dele eles voltam a ser blocos empilhados — escrever
       `opacity: 0` ali apaga metade da seção no meio da página. */
    const fixado = matchMedia("(min-width: 861px)");

    if (itens.length) {
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

        /* Mesmo princípio do vídeo do topo, aplicado a cada produto: o
           filme não toca, ele é arrastado.

           A janela de cada um vale exatamente uma unidade de posição —
           sempre a mesma, para todos. Isso é o que garante que os filmes
           corram na mesma velocidade: uma janela de duas unidades para o
           item do meio e de uma para os das pontas faria o mesmo gesto de
           rolagem avançar o dobro num clipe e a metade no outro.

           Nas pontas a janela encosta na borda em vez de sair dela: o
           primeiro começa no quadro 1 assim que a seção prende, e o último
           chega ao fim junto com a seção. No meio ela fica centrada no
           ponto em que o produto está inteiro na tela, então o que congela
           nas beiradas é justamente o trecho quase invisível. */
        const ultimo = itens.length - 1;
        if (ultimo >= 1) {
          trilhas.forEach(({ video, indice }) => {
            if (!Number.isFinite(video.duration) || !video.duration) return;
            const abre = Math.min(Math.max(indice - 0.5, 0), ultimo - 1);
            const local = Math.min(1, Math.max(0, posicao - abre));
            const alvo = local * video.duration;
            // Repetir o mesmo instante dispara um seek inútil por quadro de
            // rolagem, e é assim que o vídeo começa a engasgar.
            if (Math.abs(video.currentTime - alvo) > 0.01) video.currentTime = alvo;
          });
        }
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
        if (!fixado.matches) return;
        // Aba oculta não entrega requestAnimationFrame; aplica direto.
        if (document.hidden) medir();
        else if (!agendado) {
          agendado = true;
          requestAnimationFrame(medir);
        }
      };

      /* Sair do modo fixado precisa desfazer o que ele escreveu. Estilo
         embutido vence media query, então um item apagado continuaria
         apagado na versão empilhada. */
      const soltar = () => {
        itens.forEach((el) => {
          el.style.opacity = "";
          el.style.visibility = "";
          el.style.transform = "";
        });
        pontos.forEach((el) => el.classList.remove("ativo"));
      };

      const ajustarModo = () => {
        document.documentElement.classList.toggle("js-vitrine", fixado.matches);
        if (fixado.matches) medir();
        else soltar();
        // Empilhado ninguém arrasta o filme, então ele toca sozinho — parar
        // num quadro só seria pior do que a foto que ele substituiu.
        trilhas.forEach(({ video }) => {
          video.loop = !fixado.matches;
          if (fixado.matches) video.pause();
          // Rejeita se o navegador barrar autoplay; `muted` + `playsinline`
          // cobrem os casos atuais, mas promessa solta vira erro no console.
          else video.play().catch(() => {});
        });
      };

      addEventListener("scroll", aoRolar, { passive: true });
      addEventListener("resize", aoRolar);
      fixado.addEventListener("change", ajustarModo);
      ajustarModo();
    }

    /* ---------- 5. filmes de produto ---------- */

    if (trilhas.length) {
      /* Revelar antes de haver quadro decodificado mostraria um retângulo
         vazio no lugar da foto. `loadeddata` é o primeiro momento em que
         existe imagem para pintar.

         É também o único momento em que dá para mandar tocar: o arquivo
         chega bem depois da abertura, e um `play()` disparado no início,
         num elemento ainda sem `src`, rejeita e não volta mais. */
      trilhas.forEach(({ video, quadro }) => {
        const revelar = () => {
          if (quadro) quadro.classList.add("filme-pronto");
          if (fixado.matches) return;
          video.loop = true;
          video.play().catch(() => {});
        };
        video.addEventListener("loadeddata", revelar, { once: true });
        if (video.readyState >= 2) revelar();
      });

      /* Em fila, não os três de uma vez.

         São ~6MB por filme. Disparados juntos eles dividem a banda e
         chegam juntos lá no fim — e o visitante precisa do primeiro
         primeiro. Em fila, o primeiro recebe a linha inteira e fica pronto
         em um terço do tempo; os outros baixam enquanto ele já está sendo
         assistido. */
      const ESPERA_FILA = 6000;

      const enfileirar = (i) => {
        const trilha = trilhas[i];
        if (!trilha || trilha.video.src) return;
        // ~6MB por filme, três deles. Com economia de dados ligada, ou em
        // rede inviável, a fila nem começa: a foto do produto já está na tela
        // e é o que o filme substituiria. Mesma regra do filme de abertura
        // (rede.js), e ela diz no console quando poupa — "não funciona" e
        // "foi poupado" se investigam de formas opostas.
        if (window.RedeCara && window.RedeCara()) {
          if (window.RedeExplica) window.RedeExplica("filme do produto");
          return;
        }
        const { video } = trilha;
        video.preload = "auto";
        video.src = video.dataset.fonte;

        const seguir = () => enfileirar(i + 1);
        video.addEventListener("canplaythrough", seguir, { once: true });
        // Rede ruim pode nunca chegar a `canplaythrough`. Sem este prazo a
        // fila trava no primeiro e os outros dois nunca saem do lugar.
        setTimeout(seguir, ESPERA_FILA);
      };

      /* A maior parte das visitas nunca chega até aqui. A fila só começa
         quando a seção se aproxima, com folga de duas telas. Se a
         requisição nunca partir, a foto permanece — nada quebra. */
      const olho = new IntersectionObserver(
        (entradas, observador) => {
          if (!entradas.some((e) => e.isIntersecting)) return;
          observador.disconnect();
          enfileirar(0);
        },
        { rootMargin: "200% 0px" }
      );
      olho.observe(vitrine);
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
