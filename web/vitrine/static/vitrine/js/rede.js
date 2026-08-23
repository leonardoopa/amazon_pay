/* Uma pergunta só, respondida em um lugar só: vale a pena baixar vídeo agora?
   ---------------------------------------------------------------------------
   A página tem 12MB de filme no topo e ~6MB por produto na vitrine. Em fibra
   isso é o efeito que sustenta o argumento do site. Em 4G de celular, é o
   visitante pagando franquia de dados para ver uma animação — e esperando
   segundos antes da primeira frase aparecer.

   Mora em arquivo próprio porque quem decide isso são dois scripts (o filme
   de abertura e os filmes de produto), e a regra não pode divergir entre os
   dois: metade da página economizando dados e a outra metade não é pior do
   que qualquer das duas escolhas inteiras.

   Carregado antes dos outros dois no base.html; `defer` preserva a ordem. */

(() => {
  "use strict";

  /* Só rede realmente inviável. `3g` ficou FORA, e a razão é medida:

     `effectiveType` não diz qual é o rádio, diz o que o Chrome estimou de RTT
     e vazão nas últimas requisições. Em 4G comum de celular ele reporta "3g"
     com frequência — e reportou aqui, num link doméstico, `downlink: 0.35`
     com `effectiveType: "4g"`. Com "3g" na lista, quem tem 4G mediano perdia
     os três filmes da vitrine e via só a foto: exatamente o "parece estático"
     que apareceu no primeiro teste em celular. Bloquear o efeito principal da
     página por causa de uma estimativa ruidosa custa mais do que os 6MB. */
  const INVIAVEIS = new Set(["slow-2g", "2g"]);

  /* `true` quando baixar vídeo custa caro para quem está do outro lado.

     Na dúvida devolve `false`: o Safari e o Firefox não implementam
     `navigator.connection`, e assumir rede ruim por falta de informação
     apagaria o efeito para a maior parte dos visitantes de iPhone. */
  window.RedeCara = function RedeCara() {
    const conexao =
      navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    if (!conexao) return false;

    // Economia de dados ligada é escolha explícita de quem navega. Respeitar
    // isso não é otimização, é obedecer o que a pessoa pediu. É também o caso
    // mais comum de filme não aparecer num celular: o modo vem ligado de
    // fábrica em vários aparelhos.
    if (conexao.saveData === true) return true;

    return INVIAVEIS.has(conexao.effectiveType);
  };

  /* Diz no console por que o filme não entrou.

     Sem isto, "o vídeo não funciona" é indistinguível de "o vídeo foi
     poupado de propósito" — e as duas coisas se investigam de formas
     opostas. Uma linha no console resolve em cinco segundos. */
  window.RedeExplica = function RedeExplica(oque) {
    const conexao =
      navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    const motivo = conexao && conexao.saveData ? "economia de dados ligada" : "rede lenta";
    console.info(
      `[vitrine] ${oque} não foi baixado: ${motivo} ` +
        `(effectiveType=${conexao ? conexao.effectiveType : "?"}, ` +
        `saveData=${conexao ? conexao.saveData : "?"}). A foto fica no lugar.`
    );
  };
})();
