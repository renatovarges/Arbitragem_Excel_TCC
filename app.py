import streamlit as st
import traceback

st.set_page_config(
    page_title="Arbitragem — Planilha Excel",
    page_icon="📊",
    layout="centered",
)

st.title("📊 Análise de Arbitragem — Planilha Excel")
st.caption("Escolha a rodada e baixe os dados completos em Excel.")

try:
    import json
    from pathlib import Path
    import pandas as pd
    import arbitragem_scraper as scraper
    from excel_builder import build_excel

    # ── Rodadas disponíveis ──────────────────────────────────────────────────
    with open(Path(__file__).parent / "rodadas.json", encoding="utf-8") as f:
        rodadas_data = json.load(f)
    rodadas_disponiveis = sorted(int(k) for k in rodadas_data.keys())

    # ── Sidebar ─────────────────────────────────────────────────────────────
    st.sidebar.header("⚙️ Configurações")
    rodada_sel = st.sidebar.selectbox(
        "Rodada",
        rodadas_disponiveis,
        index=min(16, len(rodadas_disponiveis) - 1),
        format_func=lambda x: f"Rodada {x}",
    )

    with st.sidebar.expander("ℹ️ Sobre a planilha"):
        st.markdown("""
        O sistema detecta os árbitros automaticamente quando o
        Transfermarkt já publicou a escala. Para jogos ainda sem
        escala, selecione o árbitro no menu (a escala da CBF sai antes).
        As estatísticas vêm sozinhas.
        """)

    # ── Carregar escala automática + lista de árbitros ───────────────────────
    confrontos_base = rodadas_data.get(str(rodada_sel), [])

    with st.spinner("Verificando escala e árbitros disponíveis..."):
        escala_auto = scraper.get_escala_auto(rodada_sel)
        lista_arbitros = scraper.get_lista_arbitros()

    n_auto = len(escala_auto)
    if n_auto == len(confrontos_base) and n_auto > 0:
        st.success(f"✅ Escala detectada automaticamente ({n_auto}/{len(confrontos_base)} jogos).")
    elif n_auto > 0:
        st.info(f"ℹ️ {n_auto}/{len(confrontos_base)} jogos com escala automática. "
                "Complete os demais nos menus abaixo.")
    else:
        st.warning("⚠️ Escala ainda não publicada no Transfermarkt. "
                   "Selecione os árbitros manualmente abaixo "
                   "(veja a escala no site da CBF).")

    # ── Seleção de árbitros ──────────────────────────────────────────────────
    st.subheader(f"🧑‍⚖️ Árbitros — Rodada {rodada_sel}")
    opcoes = ["A confirmar"] + lista_arbitros
    escala_final = {}
    cols = st.columns(2)
    for i, c in enumerate(confrontos_base):
        key = (scraper._norm(c["mandante"]), scraper._norm(c["visitante"]))
        auto = escala_auto.get(key, "")
        idx = opcoes.index(auto) if auto in opcoes else 0
        escala_final[i] = cols[i % 2].selectbox(
            f"{c['mandante']} × {c['visitante']}",
            opcoes, index=idx, key=f"arb_{rodada_sel}_{i}",
        )

    st.divider()

    # ── Geração ─────────────────────────────────────────────────────────────
    if st.button("📊 GERAR PLANILHA EXCEL", use_container_width=True, type="primary"):
        with st.spinner("Buscando estatísticas e faltas (pode levar ~1 min)..."):
            try:
                dados = scraper.montar_dados(rodada_sel, escala_final)
            except Exception as e:
                st.error(f"Erro ao buscar dados: {e}")
                st.code(traceback.format_exc())
                st.stop()

        # Preview
        st.subheader("📊 Dados coletados")
        df_preview = pd.DataFrame([{
            "Mandante":  d["mandante"],
            "Visitante": d["visitante"],
            "Árbitro":   d["arbitro"],
            "Jogos":     d["jogos"],
            "Pênaltis":  d["penaltis"],
            "Faltas (Média)":    d["faltas_media"],
            "Amarelos (Média)":  d["amarelos_media"],
            "Vermelhos (Total)": d["vermelhos_total"],
        } for d in dados])
        st.dataframe(df_preview, use_container_width=True)

        # Excel
        with st.spinner("Montando planilha Excel..."):
            try:
                excel_bytes = build_excel(dados, rodada_sel)
                st.success("Planilha gerada! ✅")
                st.download_button(
                    label="⬇️ BAIXAR PLANILHA EXCEL",
                    data=excel_bytes,
                    file_name=f"Arbitragem_Rodada_{rodada_sel}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            except Exception as e:
                st.error(f"Erro ao gerar Excel: {e}")
                st.code(traceback.format_exc())

except Exception as e:
    st.error(f"⚠️ Erro inesperado: {e}")
    st.code(traceback.format_exc())
