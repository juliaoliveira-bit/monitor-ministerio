"""
Monitor de Ministérios — scraper.py

O que este script faz:
  1. Para cada ministério configurado em MINISTERIOS, abre a página de
     notícias e extrai título, data, resumo e link de cada notícia.
  2. Para cada ministério, abre a página de "Agenda de Autoridades" e
     extrai o texto/resumo da agenda do ministro (a maioria dos sites
     do gov.br mostra só um resumo público — a agenda detalhada fica
     no e-Agendas da CGU, que não dá para raspar por exigir login/JS).
  3. Salva tudo em data/ministerios.json, que é o arquivo que o
     dashboard (index.html) lê para exibir os dados.

Como rodar sozinho, no seu computador (para testar antes de subir pro
GitHub):
    pip install -r requirements.txt
    python scraper.py

IMPORTANTE — leia antes de usar:
  Os sites do governo (Plone/gov.br) mudam de layout de vez em quando,
  e cada ministério pode ter pequenas variações na estrutura HTML. Este
  script tenta vários seletores (fallbacks) para aumentar a chance de
  funcionar em todos, mas se um ministério específico não retornar
  nada, o mais provável é que o seletor CSS precise de ajuste — veja a
  seção "SE UM MINISTÉRIO PARAR DE FUNCIONAR" no README.md.
"""

import json
import os
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# 1. CONFIGURAÇÃO DOS MINISTÉRIOS
# ---------------------------------------------------------------------------
# Para adicionar um novo ministério, copie um bloco e ajuste as 3 URLs.
# "agenda_url" é a página resumida de "Agenda de Autoridades" do site do
# próprio ministério (não o e-Agendas da CGU, que exige JS/login).
#
# ATENÇÃO: a URL de agenda do MEC abaixo é uma tentativa baseada no padrão
# dos outros ministérios (.../pt-br/acesso-a-informacao/agenda-de-autoridades)
# mas eu não consegui confirmá-la — verifique manualmente antes de confiar
# nela e ajuste se for necessário (veja o README).

MINISTERIOS = {
    "mj": {
        "nome": "Ministério da Justiça e Segurança Pública",
        "noticias_url": "https://www.gov.br/mj/pt-br/assuntos/noticias-1",
        "agenda_url": "https://www.gov.br/mj/pt-br/acesso-a-informacao/agenda-de-autoridades",
    },
    "mec": {
        "nome": "Ministério da Educação",
        "noticias_url": "https://www.gov.br/mec/pt-br/assuntos/noticias",
        "agenda_url": "https://www.gov.br/mec/pt-br/acesso-a-informacao/agenda-de-autoridades",  # NÃO CONFIRMADA — verificar
    },
    "mcti": {
        "nome": "Ministério da Ciência, Tecnologia e Inovação",
        "noticias_url": "https://www.gov.br/mcti/pt-br/acompanhe-o-mcti/noticias",
        "agenda_url": "https://www.gov.br/mcti/pt-br/acesso-a-informacao/agenda-de-autoridades/agenda-ministro",
    },
}

HEADERS = {
    # Alguns sites do governo bloqueiam requisições sem um User-Agent "normal"
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
}

TIMEOUT = 20  # segundos de espera por resposta antes de desistir
MAX_NOTICIAS_POR_MINISTERIO = 10


def buscar_html(url):
    """Baixa uma página e devolve um objeto BeautifulSoup, ou None se falhar."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as erro:
        print(f"  [ERRO] Não consegui abrir {url}: {erro}")
        return None


def extrair_noticias(soup, base_url):
    """
    Extrai a lista de notícias de uma página de listagem do gov.br.
    Estrutura confirmada (inspecionada diretamente no MJ em 22/07/2026):

        <ul class="noticias ...">
          <li>
            <div class="conteudo">
              <div class="subtitulo-noticia">CATEGORIA</div>
              <h2 class="titulo"><a href="...">Título</a></h2>
              <span class="descricao">
                <span class="data">DD/MM/AAAA</span> - Resumo da notícia
              </span>
            </div>
          </li>
        </ul>

    Mantemos alguns seletores alternativos como fallback, caso algum
    ministério específico use uma variação de tema diferente.
    """
    noticias = []
    if soup is None:
        return noticias

    itens = (
        soup.select("ul.noticias li")
        or soup.select("li.tileItem")
        or soup.select("article.tileItem")
        or soup.select("div.tileItem")
    )
    print(f"  (diagnóstico: {len(itens)} candidato(s) bruto(s) encontrados na página)")

    for item in itens[:MAX_NOTICIAS_POR_MINISTERIO]:
        link_tag = item.select_one("h2.titulo a, h2 a, h3 a, a.summary")
        if not link_tag:
            continue

        titulo = link_tag.get_text(strip=True)
        link = link_tag.get("href", "")
        if link and not link.startswith("http"):
            link = base_url.rstrip("/") + "/" + link.lstrip("/")

        data_tag = item.select_one("span.data")
        data_publicacao = data_tag.get_text(strip=True) if data_tag else ""
        if not data_publicacao:
            # fallback: procura qualquer data solta no texto do item
            match_data = re.search(r"\d{2}/\d{2}/\d{4}", item.get_text(" ", strip=True))
            data_publicacao = match_data.group(0) if match_data else ""

        resumo = ""
        descricao_tag = item.select_one("span.descricao")
        if descricao_tag:
            texto = descricao_tag.get_text(" ", strip=True)
            # remove a data e o traço do início, deixando só o texto do resumo
            resumo = re.sub(r"^\d{2}/\d{2}/\d{4}\s*-?\s*", "", texto).strip()
        else:
            resumo_tag = item.select_one("p.description, span.description")
            resumo = resumo_tag.get_text(strip=True) if resumo_tag else ""

        if titulo:
            noticias.append(
                {
                    "titulo": titulo,
                    "link": link,
                    "data": data_publicacao,
                    "resumo": resumo,
                }
            )

    return noticias


def extrair_resumo_agenda(soup):
    """
    Extrai o texto resumido da página de 'Agenda de Autoridades'.
    Essas páginas normalmente têm um bloco de texto curto (ex: "Atualmente
    não existem compromissos agendados") e um link para o e-Agendas com a
    agenda completa. Aqui pegamos só o texto visível, não o e-Agendas em si.
    """
    if soup is None:
        return {"resumo": "", "link_agenda_completa": ""}

    conteudo = soup.select_one("#content-core") or soup.select_one("main")
    resumo = conteudo.get_text(" ", strip=True)[:500] if conteudo else ""

    link_completo = ""
    link_tag = soup.find("a", href=lambda h: h and "eagendas.cgu.gov.br" in h)
    if link_tag:
        link_completo = link_tag.get("href", "")

    return {"resumo": resumo, "link_agenda_completa": link_completo}


def main():
    resultado = {
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
        "ministerios": {},
    }

    for sigla, info in MINISTERIOS.items():
        print(f"Processando {info['nome']} ({sigla})...")

        base_url = "https://www.gov.br"

        soup_noticias = buscar_html(info["noticias_url"])
        noticias = extrair_noticias(soup_noticias, base_url)
        print(f"  -> {len(noticias)} notícias encontradas")

        time.sleep(1)  # pequena pausa entre requisições, por educação com o servidor

        soup_agenda = buscar_html(info["agenda_url"])
        agenda = extrair_resumo_agenda(soup_agenda)

        resultado["ministerios"][sigla] = {
            "nome": info["nome"],
            "noticias": noticias,
            "agenda": agenda,
        }

        time.sleep(1)

    os.makedirs("data", exist_ok=True)  # cria a pasta se ela não existir no repositório
    with open("data/ministerios.json", "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)

    print("\nConcluído. Dados salvos em data/ministerios.json")


if __name__ == "__main__":
    main()
