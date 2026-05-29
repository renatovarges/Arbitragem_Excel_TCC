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
        A planilha Excel gerada contém todos os jogos da rodada com
        árbitro e estatísticas, formatação com cores por coluna e
        cabeçalhos congelados.

        > Se a escala ainda não foi divulgada, o árbitro aparece
        > como **"A confirmar"**.
        """)

    # ── Preview dos confrontos ──────────────────────────────────────────────
    confrontos_base = rodadas_data.get(str(rodada_sel), [])
    st.subheader(f"📅 Confrontos — Rodada {rodada_sel}")
    cols = st.columns(2)
    for i, c in enumerate(confrontos_base):
        cols[i % 2].markdown(f"**{c['mandante']}** × {c['visitante']}")

    st.divider()

    # ── Geração ─────────────────────────────────────────────────────────────
    if st.button("📊 GERAR PLANILHA EXCEL", use_container_width=True, type="primary"):
        with st.spinner("Buscando árbitros e estatísticas (pode levar até 30s)..."):
            try:
                dados = scraper.get_rodada_completa(rodada_sel)
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

        confirmados = [d for d in dados if d["arbitro"] != "A confirmar"]
        if not confirmados:
            st.warning(
                "⚠️ A escala desta rodada ainda não foi divulgada. "
                "A planilha será gerada com 'A confirmar' nos campos de árbitro."
            )

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
