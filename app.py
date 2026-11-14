
import streamlit as st
from semantic_validator import validate_sql
import requests
import duckdb


def verify_revenue(sql):
    reference_sql = """
    SELECT SUM(oi.quantity * oi.unit_price)
    FROM orders o
    JOIN order_items oi
        ON o.order_id = oi.order_id
    WHERE o.order_status = 'completed'
    """

    con = duckdb.connect(
        "data/ecommerce.duckdb",
        read_only=True
    )

    try:
        reference = con.execute(reference_sql).fetchone()[0]

        # Only execute a single read only SELECT query.
        import sqlglot
        from sqlglot import exp

        statements = sqlglot.parse(sql, read="duckdb")

        if len(statements) != 1:
            raise ValueError("Exactly one SQL statement is required.")

        tree = statements[0]

        if not isinstance(tree, exp.Select):
            raise ValueError("Only SELECT queries are allowed.")

        forbidden = (
            exp.Insert, exp.Update, exp.Delete,
            exp.Create, exp.Drop, exp.Alter,
            exp.Command
        )

        if any(tree.find(node) for node in forbidden):
            raise ValueError("Unsupported SQL operation.")

        generated = con.execute(sql).fetchone()

        if generated is None or len(generated) != 1:
            raise ValueError("Expected one result column.")

        actual = generated[0]

        correct = (
            actual is not None
            and abs(float(actual) - float(reference)) <= 0.01
        )

        return actual, reference, correct

    finally:
        con.close()


st.set_page_config(
    page_title="SQL Semantic Validator",
    page_icon="🔍",
    layout="wide"
)

st.title("SQL Semantic Validator")
st.caption("Business Logic Validation for AI Generated SQL")

st.divider()

generate_tab, validate_tab = st.tabs([
    "Generate SQL",
    "Validate SQL"
])

with generate_tab:
    st.subheader("Generate SQL with Mistral")

    business_question = st.text_area(
        "Enter your business question",
        placeholder="What is our total sales revenue?",
        key="generate_question"
    )

    use_semantics = st.checkbox(
        "Include business metric definitions",
        value=True
    )

    if st.button("Generate SQL", type="primary"):
        if not business_question.strip():
            st.warning("Please enter a business question.")
        else:
            with st.spinner("Generating SQL using Mistral..."):
                try:
                    from run_experiment import generate

                    generated_sql = generate(
                        business_question,
                        use_semantics
                    )

                    st.session_state["generated_sql"] = generated_sql
                    st.session_state["generated_question"] = business_question

                except requests.exceptions.RequestException:
                    st.error(
                        "Cannot connect to Ollama. "
                        "Please check whether Ollama is running."
                    )
                except Exception as error:
                    st.error(f"SQL generation failed: {error}")

    
    if "generated_sql" in st.session_state:
        st.subheader("Generated SQL")
        st.code(
            st.session_state["generated_sql"],
            language="sql"
        )

        if st.button("Evaluate SQL", type="primary"):
            question = st.session_state["generated_question"]
            sql = st.session_state["generated_sql"]

            st.subheader("Evaluation Results")

            validation = validate_sql(question, sql)

            col1, col2 = st.columns(2)

            with col1:
                st.markdown("**Semantic Validation**")

                if validation["passed"]:
                    st.success("Passed")
                else:
                    st.error("Failed")
                    for issue in validation["issues"]:
                        st.write(issue)

            with col2:
                st.markdown("**SQL Execution and Numerical Verification**")

                if question.strip().lower().rstrip("?").strip() != (
                    "what is our total sales revenue"
                ):
                    st.info(
                        "Reference verification is available "
                        "for the total sales revenue question."
                    )
                else:
                    try:
                        actual, reference, correct = verify_revenue(sql)

                        st.success("SQL executed successfully")

                        st.metric(
                            "Generated Revenue",
                            f"${float(actual):,.2f}"
                            if actual is not None else "NULL"
                        )

                        st.metric(
                            "Verified Revenue",
                            f"${float(reference):,.2f}"
                        )

                        if correct:
                            st.success("Numerical Verification Passed")
                        else:
                            st.error("Numerical Verification Failed")

                    except Exception as error:
                        st.error(f"SQL execution failed: {error}")



with validate_tab:
    st.subheader("Evaluate SQL Query")

    question = st.text_area(
        "Business Question",
        value="What is our total sales revenue?",
        height=80
    )

    sql = st.text_area(
        "SQL Query",
        placeholder="Paste your SQL query here...",
        height=220
    )

    if st.button("Evaluate Pasted SQL", type="primary"):
        if not question.strip() or not sql.strip():
            st.warning("Please enter both the question and SQL.")
        else:
            validation = validate_sql(question, sql)

            st.subheader("Evaluation Results")

            col1, col2 = st.columns(2)

            with col1:
                st.markdown("**Semantic Validation**")

                if validation["passed"]:
                    st.success("Passed")
                else:
                    st.error("Failed")
                    for issue in validation["issues"]:
                        st.write(issue)

            with col2:
                st.markdown("**SQL Execution and Numerical Verification**")

                if question.strip().lower().rstrip("?").strip() != (
                    "what is our total sales revenue"
                ):
                    st.info(
                        "Reference verification is currently available "
                        "only for total sales revenue."
                    )
                else:
                    try:
                        actual, reference, correct = verify_revenue(sql)

                        st.success("SQL executed successfully")

                        st.metric(
                            "Generated Revenue",
                            f"${float(actual):,.2f}"
                            if actual is not None else "NULL"
                        )

                        st.metric(
                            "Verified Revenue",
                            f"${float(reference):,.2f}"
                        )

                        if correct:
                            st.success("Numerical Verification Passed")
                        else:
                            st.error("Numerical Verification Failed")

                    except Exception as error:
                        st.error(f"SQL execution failed: {error}")


st.divider()
st.caption("Python | Mistral | Ollama | SQLGlot | DuckDB")
