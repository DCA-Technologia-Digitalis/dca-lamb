from flask import Blueprint, request, jsonify
from database import db, Documento

documentos_bp = Blueprint('documentos', __name__, url_prefix='/api/documentos')

@documentos_bp.route('/', methods=['GET'])
def get_documentos():
    documentos = Documento.query.all()
    return jsonify([{'id': d.id, 'nome': d.nome} for d in documentos]), 200

@documentos_bp.route('/<int:id>', methods=['GET'])
def get_documento(id):
    documento = Documento.query.get_or_404(id)
    return jsonify({'id': documento.id, 'nome': documento.nome}), 200

@documentos_bp.route('/', methods=['POST'])
def create_documeto():
    data = request.get_json()
    nome = data['nome']

    new_documento = Documento(nome=nome)
    db.session.add(new_documento)
    db.session.commit()
    return jsonify({'id': new_documento.id, 'nome': new_documento.nome}), 201