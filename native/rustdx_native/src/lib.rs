//! Narrow Python bridge; all market-data decoding is performed by rustdx.
use chrono::NaiveDate;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use rustdx_complete::calendar::TradingCalendar;
use rustdx_complete::tcp::stock::{BlockInfoChunk, BlockInfoMeta, Client, parse_block_content};
use rustdx_complete::tcp::{TcpConfig, Tdx};
use serde::Serialize;
use serde_json::{Value, json};
use std::net::SocketAddr;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::Mutex;
use std::time::Duration;

fn invalid(message: impl ToString) -> PyErr {
    PyValueError::new_err(message.to_string())
}

fn runtime(message: impl ToString) -> PyErr {
    PyRuntimeError::new_err(message.to_string())
}

fn security(market: u16, code: &str) -> PyResult<()> {
    if !matches!(market, 0 | 1) || code.len() != 6 || !code.bytes().all(|b| b.is_ascii_digit()) {
        return Err(invalid(
            "market must be 0/1 and code must contain six digits",
        ));
    }
    Ok(())
}

fn serialize(value: &impl Serialize) -> Result<String, String> {
    serde_json::to_string(value).map_err(|e| e.to_string())
}

#[pyclass]
struct QuoteClient {
    client: Mutex<Option<Client>>,
}

impl QuoteClient {
    fn call<T: Send>(
        &self,
        py: Python<'_>,
        operation: impl FnOnce(&mut Client) -> Result<T, String> + Send,
    ) -> PyResult<T> {
        py.allow_threads(|| {
            let mut guard = self.client.lock().map_err(|e| runtime(e.to_string()))?;
            let client = guard
                .as_mut()
                .ok_or_else(|| runtime("rustdx connection is closed"))?;
            match catch_unwind(AssertUnwindSafe(|| operation(client))) {
                Ok(result) => result.map_err(runtime),
                Err(_) => {
                    *guard = None;
                    Err(runtime(
                        "rustdx protocol decoder panicked; connection closed",
                    ))
                }
            }
        })
    }
}

#[pymethods]
impl QuoteClient {
    #[new]
    #[pyo3(signature = (server, timeout_seconds=5))]
    fn new(py: Python<'_>, server: String, timeout_seconds: u64) -> PyResult<Self> {
        let ip = server.parse::<SocketAddr>().map_err(invalid)?;
        let config = TcpConfig {
            ip: Some(ip),
            timeout: Duration::from_secs(timeout_seconds.clamp(1, 60)),
            auto_reconnect: 0,
            retry_delay_ms: 0,
            recheck_empty: false,
        };
        let client = py.allow_threads(|| {
            catch_unwind(AssertUnwindSafe(|| Client::with_config(&config)))
                .map_err(|_| runtime("rustdx handshake panicked"))?
                .map_err(runtime)
        })?;
        Ok(Self {
            client: Mutex::new(Some(client)),
        })
    }

    fn stocks_json(&self, py: Python<'_>, market: u16) -> PyResult<String> {
        security(market, "000001")?;
        self.call(py, |c| {
            serialize(&c.stocks(market).map_err(|e| e.to_string())?)
        })
    }

    fn quotes_json(&self, py: Python<'_>, securities: Vec<(u16, String)>) -> PyResult<String> {
        for (market, code) in &securities {
            security(*market, code)?;
        }
        if securities.len() > 80 {
            return Err(invalid("quote batch exceeds 80 securities"));
        }
        self.call(py, |c| {
            let pairs = securities
                .iter()
                .map(|(m, code)| (*m, code.as_str()))
                .collect::<Vec<_>>();
            serialize(&c.quotes(&pairs).map_err(|e| e.to_string())?)
        })
    }

    #[pyo3(signature = (market, code, category, start=0, count=800, index=false))]
    fn bars_json(
        &self,
        py: Python<'_>,
        market: u16,
        code: String,
        category: u16,
        start: u16,
        count: u16,
        index: bool,
    ) -> PyResult<String> {
        security(market, &code)?;
        if category > 11 || count == 0 || count > 800 {
            return Err(invalid("invalid bar category/count"));
        }
        self.call(py, |c| {
            if index {
                // The index daily protocol uses category 4, not equity category 9.
                let category = if category == 9 { 4 } else { category };
                serialize(
                    &c.index_bars(market, &code, category, start, count)
                        .map_err(|e| e.to_string())?,
                )
            } else {
                serialize(
                    &c.bars(market, &code, category, start, count)
                        .map_err(|e| e.to_string())?,
                )
            }
        })
    }

    fn minutes_json(
        &self,
        py: Python<'_>,
        market: u16,
        code: String,
        date: u32,
    ) -> PyResult<String> {
        security(market, &code)?;
        NaiveDate::parse_from_str(&date.to_string(), "%Y%m%d").map_err(invalid)?;
        self.call(py, |c| {
            serialize(
                &c.history_minute(market, &code, date)
                    .map_err(|e| e.to_string())?,
            )
        })
    }

    #[pyo3(signature = (market, code, date, start=0, count=2000))]
    fn transactions_json(
        &self,
        py: Python<'_>,
        market: u16,
        code: String,
        date: u32,
        start: u16,
        count: u16,
    ) -> PyResult<String> {
        security(market, &code)?;
        NaiveDate::parse_from_str(&date.to_string(), "%Y%m%d").map_err(invalid)?;
        if count == 0 || count > 2000 {
            return Err(invalid("transaction count must be 1..2000"));
        }
        self.call(py, |c| {
            serialize(
                &c.history_transaction(market, &code, start, count, date)
                    .map_err(|e| e.to_string())?,
            )
        })
    }

    fn finance_json(&self, py: Python<'_>, market: u16, code: String) -> PyResult<String> {
        security(market, &code)?;
        self.call(py, |c| {
            serialize(&c.finance(market, &code).map_err(|e| e.to_string())?)
        })
    }

    fn xdxr_json(&self, py: Python<'_>, market: u16, code: String) -> PyResult<String> {
        security(market, &code)?;
        self.call(py, |c| {
            let rows = c.xdxr(market, &code).map_err(|e| e.to_string())?;
            let values: Vec<Value> = rows
                .into_iter()
                .map(|x| {
                    let mut row = json!({
                        "market": x.market, "code": x.code,
                        "year": x.date / 10000, "month": x.date % 10000 / 100, "day": x.date % 100,
                        "category": x.category,
                    });
                    let fields = if x.category == 1 || (11..=14).contains(&x.category) {
                        ["fenhong", "peigujia", "songzhuangu", "peigu"]
                    } else {
                        [
                            "panqianliutong",
                            "qianzongguben",
                            "panhouliutong",
                            "houzongguben",
                        ]
                    };
                    for (key, value) in fields
                        .into_iter()
                        .zip([x.fh_qltp, x.pgj_qzgb, x.sg_hltp, x.pg_hzgb])
                    {
                        row[key] = json!(value);
                    }
                    row
                })
                .collect();
            serialize(&values)
        })
    }

    fn f10_json(&self, py: Python<'_>, market: u16, code: String) -> PyResult<String> {
        security(market, &code)?;
        self.call(py, |c| {
            serialize(&c.f10(market, &code).map_err(|e| e.to_string())?)
        })
    }

    fn file_bytes<'py>(&self, py: Python<'py>, filename: String) -> PyResult<Bound<'py, PyBytes>> {
        if ![
            "block.dat",
            "block_gn.dat",
            "block_fg.dat",
            "block_zs.dat",
            "tdxhy.cfg",
        ]
        .contains(&filename.as_str())
        {
            return Err(invalid("unsupported reference filename"));
        }
        let bytes = self.call(py, |c| {
            let mut meta = BlockInfoMeta::new(&filename);
            meta.recv_parsed(&mut c.tcp).map_err(|e| e.to_string())?;
            let size = *meta.result();
            if size == 0 || size > 64 * 1024 * 1024 {
                return Err("empty or oversized rustdx reference file".to_string());
            }
            let mut data = Vec::with_capacity(size as usize);
            for start in (0..size).step_by(0x7530) {
                let length = (size - start).min(0x7530);
                let mut chunk = BlockInfoChunk::new(&filename, start, length);
                chunk.recv_parsed(&mut c.tcp).map_err(|e| e.to_string())?;
                if chunk.result().len() != length as usize {
                    return Err("truncated rustdx reference file".to_string());
                }
                data.extend_from_slice(chunk.result());
            }
            Ok(data)
        })?;
        Ok(PyBytes::new(py, &bytes))
    }

    fn close(&self, py: Python<'_>) -> PyResult<()> {
        py.allow_threads(|| {
            self.client
                .lock()
                .map_err(|e| runtime(e.to_string()))?
                .take();
            Ok(())
        })
    }
}

#[pyfunction]
fn block_records_json(content: &[u8]) -> PyResult<String> {
    if content.len() < 386 {
        return Err(invalid("truncated block file"));
    }
    serialize(&parse_block_content(content)).map_err(runtime)
}

#[pyfunction]
fn is_trading_day(value: String) -> PyResult<bool> {
    let date = NaiveDate::parse_from_str(&value, "%Y-%m-%d").map_err(invalid)?;
    TradingCalendar::try_is_trading_day(&date).map_err(runtime)
}

#[pymodule]
fn banxia_rustdx_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<QuoteClient>()?;
    module.add_function(wrap_pyfunction!(block_records_json, module)?)?;
    module.add_function(wrap_pyfunction!(is_trading_day, module)?)?;
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add("RUSTDX_VERSION", "1.12.2")?;
    module.add(
        "RUSTDX_SOURCE_REV",
        "904b33e514d4e368e633622c96a9e63af8a53d37",
    )?;
    Ok(())
}
