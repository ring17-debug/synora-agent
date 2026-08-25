use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
};

use serde::{Deserialize, Serialize};
use synora_core::{block::Block, state::Address, transaction::Transaction};

use crate::node::{NodeError, SynoraNode};

#[derive(Debug, Deserialize)]
struct TransactionRequest {
    chain_id: u64,
    nonce: u64,
    sender: String,
    recipient: String,
    value: u64,
    gas_limit: u64,
    gas_price: u64,
    #[serde(default)]
    data: String,
}

#[derive(Debug, Serialize)]
struct TransactionAcceptedResponse {
    status: &'static str,
    hash: String,
    pending_transactions: usize,
}

#[derive(Debug, Serialize)]
struct TransactionRejectedResponse {
    status: &'static str,
    error: String,
}

pub struct RpcServer {
    address: String,
}

impl RpcServer {
    pub fn new(address: impl Into<String>) -> Self {
        Self {
            address: address.into(),
        }
    }

    pub fn address(&self) -> &str {
        &self.address
    }

    pub fn run(&self, node: &mut SynoraNode) -> std::io::Result<()> {
        let listener = TcpListener::bind(&self.address)?;

        println!();
        println!("=================================");
        println!("         SYNORA RPC SERVER       ");
        println!("=================================");
        println!("RPC Address    : http://{}", self.address);
        println!("Status         : LISTENING");
        println!();

        for stream in listener.incoming() {
            match stream {
                Ok(mut stream) => {
                    if let Err(error) = handle_connection(&mut stream, node) {
                        eprintln!("RPC error: {}", error);
                    }
                }

                Err(error) => {
                    eprintln!("Connection error: {}", error);
                }
            }
        }

        Ok(())
    }
}

fn handle_connection(stream: &mut TcpStream, node: &mut SynoraNode) -> std::io::Result<()> {
    let mut buffer = [0u8; 16 * 1024];

    let size = stream.read(&mut buffer)?;

    if size == 0 {
        return Ok(());
    }

    let request = String::from_utf8_lossy(&buffer[..size]);

    let mut lines = request.lines();

    let request_line = match lines.next() {
        Some(line) => line,

        None => {
            return send_response(stream, 400, r#"{"error":"invalid HTTP request"}"#);
        }
    };

    let mut parts = request_line.split_whitespace();

    let method = parts.next().unwrap_or("");
    let path = parts.next().unwrap_or("/");

    println!("RPC            : {} {}", method, path);

    if method == "POST" && path == "/transaction" {
        let body = match extract_body(&request) {
            Some(body) => body,

            None => {
                return send_response(stream, 400, r#"{"error":"request body is required"}"#);
            }
        };

        return handle_submit_transaction(stream, node, body);
    }

    match (method, path) {
        ("GET", "/") => send_response(
            stream,
            200,
            r#"{
  "name":"Synora",
  "version":"0.1.0",
  "service":"rpc"
}"#,
        ),

        ("GET", "/status") => {
            let json = status_json(node);
            send_response(stream, 200, &json)
        }

        ("GET", "/block/latest") => {
            let json = block_json(node.chain().latest_block());
            send_response(stream, 200, &json)
        }

        ("GET", "/mempool") => {
            let json = format!(
                r#"{{"pending_transactions":{}}}"#,
                node.pending_transactions()
            );

            send_response(stream, 200, &json)
        }

        ("POST", "/block/produce") => match node.produce_block(None) {
            Ok(block) => {
                let json = block_json(&block);
                send_response(stream, 200, &json)
            }

            Err(NodeError::NoTransactions) => {
                send_response(stream, 409, r#"{"error":"no transactions available"}"#)
            }

            Err(NodeError::BlockGasLimitExceeded) => {
                send_response(stream, 409, r#"{"error":"block gas limit exceeded"}"#)
            }

            Err(error) => {
                let json = format!(r#"{{"error":"{}"}}"#, json_escape(&format!("{:?}", error)));

                send_response(stream, 500, &json)
            }
        },

        _ => {
            if method == "GET" {
                if let Some(height) = path.strip_prefix("/block/") {
                    match height.parse::<u64>() {
                        Ok(height) => match node.chain().block(height) {
                            Some(block) => {
                                let json = block_json(block);
                                return send_response(stream, 200, &json);
                            }

                            None => {
                                return send_response(
                                    stream,
                                    404,
                                    r#"{"error":"block not found"}"#,
                                );
                            }
                        },

                        Err(_) => {
                            return send_response(
                                stream,
                                400,
                                r#"{"error":"invalid block height"}"#,
                            );
                        }
                    }
                }

                if let Some(address) = path.strip_prefix("/state/") {
                    match parse_address(address) {
                        Some(address) => match node.state().get_account(&address) {
                            Some(account) => {
                                let json = format!(
                                    r#"{{"address":"{}","balance":{},"nonce":{}}}"#,
                                    address_hex(&address),
                                    account.balance,
                                    account.nonce
                                );

                                return send_response(stream, 200, &json);
                            }

                            None => {
                                return send_response(
                                    stream,
                                    404,
                                    r#"{"error":"account not found"}"#,
                                );
                            }
                        },

                        None => {
                            return send_response(stream, 400, r#"{"error":"invalid address"}"#);
                        }
                    }
                }
            }

            send_response(stream, 404, r#"{"error":"route not found"}"#)
        }
    }
}

fn handle_submit_transaction(
    stream: &mut TcpStream,
    node: &mut SynoraNode,
    body: &str,
) -> std::io::Result<()> {
    let transaction = match parse_transaction_json(body) {
        Ok(transaction) => transaction,

        Err(error) => {
            let response = TransactionRejectedResponse {
                status: "rejected",
                error,
            };

            return send_json_response(stream, 400, &response);
        }
    };

    let transaction_hash = transaction.hash();

    match node.submit_transaction(transaction) {
        Ok(()) => {
            let response = TransactionAcceptedResponse {
                status: "accepted",
                hash: hash_hex(&transaction_hash),
                pending_transactions: node.pending_transactions(),
            };

            send_json_response(stream, 200, &response)
        }

        Err(error) => {
            let status = match error {
                NodeError::Mempool(_) => 409,
                NodeError::Chain(_) => 400,
                NodeError::NoTransactions => 409,
                NodeError::BlockGasLimitExceeded => 409,
            };

            let response = TransactionRejectedResponse {
                status: "rejected",
                error: format!("{:?}", error),
            };

            send_json_response(stream, status, &response)
        }
    }
}

fn parse_transaction_json(body: &str) -> Result<Transaction, String> {
    let request: TransactionRequest = serde_json::from_str(body)
        .map_err(|error| format!("invalid transaction JSON: {}", error))?;

    let sender =
        parse_address(&request.sender).ok_or_else(|| "invalid sender address".to_string())?;

    let recipient =
        parse_address(&request.recipient).ok_or_else(|| "invalid recipient address".to_string())?;

    let data = parse_hex_bytes(&request.data).ok_or_else(|| "invalid data hex".to_string())?;

    Ok(Transaction::new(
        request.chain_id,
        request.nonce,
        sender,
        recipient,
        request.value,
        request.gas_limit,
        request.gas_price,
        data,
    ))
}

fn extract_body(request: &str) -> Option<&str> {
    request
        .split_once("\r\n\r\n")
        .map(|(_, body)| body)
        .or_else(|| request.split_once("\n\n").map(|(_, body)| body))
}

fn status_json(node: &SynoraNode) -> String {
    format!(
        r#"{{
  "name":"Synora",
  "version":"0.1.0",
  "chain_id":{},
  "height":{},
  "block_count":{},
  "mempool":{},
  "latest_block_hash":"{}",
  "status":"ready"
}}"#,
        node.chain().chain_id(),
        node.chain().height(),
        node.chain().block_count(),
        node.pending_transactions(),
        hash_hex(&node.chain().latest_block().hash()),
    )
}

fn block_json(block: &Block) -> String {
    let transactions = block
        .transactions
        .iter()
        .map(|tx| {
            format!(
                r#"{{
      "hash":"{}",
      "chain_id":{},
      "nonce":{},
      "sender":"{}",
      "recipient":"{}",
      "value":{},
      "gas_limit":{},
      "gas_price":{}
    }}"#,
                hash_hex(&tx.hash()),
                tx.chain_id,
                tx.nonce,
                address_hex(&tx.sender),
                address_hex(&tx.recipient),
                tx.value,
                tx.gas_limit,
                tx.gas_price,
            )
        })
        .collect::<Vec<_>>()
        .join(",");

    format!(
        r#"{{
  "header":{{
    "version":{},
    "chain_id":{},
    "height":{},
    "timestamp":{},
    "previous_hash":"{}",
    "state_root":"{}",
    "transactions_root":"{}"
  }},
  "transaction_count":{},
  "transactions":[{}]
}}"#,
        block.header.version,
        block.header.chain_id,
        block.header.height,
        block.header.timestamp,
        hash_hex(&block.header.previous_hash),
        hash_hex(&block.header.state_root),
        hash_hex(&block.header.transactions_root),
        block.transactions.len(),
        transactions,
    )
}

fn send_json_response<T: Serialize>(
    stream: &mut TcpStream,
    status: u16,
    value: &T,
) -> std::io::Result<()> {
    let body =
        serde_json::to_string_pretty(value).expect("RPC response serialization should never fail");

    send_response(stream, status, &body)
}

fn send_response(stream: &mut TcpStream, status: u16, body: &str) -> std::io::Result<()> {
    let status_text = match status {
        200 => "OK",
        400 => "Bad Request",
        404 => "Not Found",
        409 => "Conflict",
        500 => "Internal Server Error",
        _ => "Unknown",
    };

    let response = format!(
        "HTTP/1.1 {} {}\r\n\
         Content-Type: application/json\r\n\
         Content-Length: {}\r\n\
         Connection: close\r\n\
         \r\n\
         {}",
        status,
        status_text,
        body.len(),
        body,
    );

    stream.write_all(response.as_bytes())?;
    stream.flush()?;

    Ok(())
}

fn address_hex(address: &Address) -> String {
    let mut result = String::with_capacity(42);

    result.push_str("0x");

    for byte in address {
        result.push_str(&format!("{:02x}", byte));
    }

    result
}

fn hash_hex(hash: &[u8; 32]) -> String {
    let mut result = String::with_capacity(66);

    result.push_str("0x");

    for byte in hash {
        result.push_str(&format!("{:02x}", byte));
    }

    result
}

fn parse_address(value: &str) -> Option<Address> {
    let value = value.strip_prefix("0x").unwrap_or(value);

    if value.len() != 40 {
        return None;
    }

    let mut address = [0u8; 20];

    for (index, chunk) in value.as_bytes().chunks(2).enumerate() {
        let text = std::str::from_utf8(chunk).ok()?;

        address[index] = u8::from_str_radix(text, 16).ok()?;
    }

    Some(address)
}

fn parse_hex_bytes(value: &str) -> Option<Vec<u8>> {
    let value = value.strip_prefix("0x").unwrap_or(value);

    if value.is_empty() {
        return Some(Vec::new());
    }

    if !value.len().is_multiple_of(2) {
        return None;
    }

    let mut result = Vec::with_capacity(value.len() / 2);

    for chunk in value.as_bytes().chunks(2) {
        let text = std::str::from_utf8(chunk).ok()?;
        result.push(u8::from_str_radix(text, 16).ok()?);
    }

    Some(result)
}

fn json_escape(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
        .replace('\t', "\\t")
}
