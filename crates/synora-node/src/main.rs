mod config;
mod node;
mod rpc;
mod rpc_client;

use config::NodeConfig;
use node::SynoraNode;
use rpc::RpcServer;
use synora_core::transaction::Transaction;

fn main() {
    println!("=================================");
    println!("        SYNORA NODE v0.1.0       ");
    println!("=================================");

    let fee_recipient = [3u8; 20];

    let config = NodeConfig::new(1337, fee_recipient, 1000, 1_000_000);

    let mut node = SynoraNode::new(config, 1_700_000_000);

    let alice = [1u8; 20];
    let bob = [2u8; 20];

    node.create_account(alice, 1_000_000);

    node.create_account(bob, 0);

    node.create_account(fee_recipient, 0);

    println!("Chain ID       : {}", node.chain().chain_id());

    println!("Block Height   : {}", node.chain().height());

    println!("Block Count    : {}", node.chain().block_count());

    println!("Mempool        : {}", node.pending_transactions());

    /*
     * Demo transaction.
     *
     * This gives us an initial transaction so that
     * the node has something to demonstrate when
     * it starts.
     */
    let tx = Transaction::new(
        node.chain().chain_id(),
        0,
        alice,
        bob,
        10_000,
        21_000,
        1,
        Vec::new(),
    );

    println!();
    println!("Submitting demo transaction...");

    match node.submit_transaction(tx) {
        Ok(()) => {
            println!("Transaction    : ACCEPTED");
        }

        Err(error) => {
            println!("Transaction    : REJECTED");
            println!("Error          : {:?}", error);
        }
    }

    println!("Mempool        : {}", node.pending_transactions());

    /*
     * Start HTTP RPC server.
     *
     * 0.0.0.0 means the RPC server listens on
     * all available network interfaces.
     */
    let rpc = RpcServer::new("0.0.0.0:8545");

    println!();
    println!("Starting Synora RPC...");
    println!("RPC Endpoint   : http://{}", rpc.address());

    if let Err(error) = rpc.run(&mut node) {
        eprintln!("RPC server failed: {}", error);
    }
}
